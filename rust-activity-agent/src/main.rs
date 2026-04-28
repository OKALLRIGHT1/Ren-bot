use anyhow::Result;
use chrono::Utc;
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use std::ptr;
use std::thread;
use std::time::{Duration, Instant};
use uuid::Uuid;
use windows_sys::Win32::Foundation::{CloseHandle, HWND};
use windows_sys::Win32::System::RemoteDesktop::WTSGetActiveConsoleSessionId;
use windows_sys::Win32::System::SystemInformation::GetTickCount;
use windows_sys::Win32::System::Threading::{
    OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
};
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    GetForegroundWindow, GetWindowTextLengthW, GetWindowTextW, GetWindowThreadProcessId,
};

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ActivityApp {
    id: String,
    name: String,
    title: Option<String>,
    pid: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct BrowserContext {
    family: String,
    name: String,
    page_title: Option<String>,
    url: Option<String>,
    domain: Option<String>,
    source: String,
    confidence: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ActivityEvent {
    event_id: String,
    ts: String,
    device_id: String,
    agent_name: String,
    platform: String,
    kind: String,
    app: ActivityApp,
    window_title: Option<String>,
    browser: Option<BrowserContext>,
    presence: String,
    source: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct LastApp {
    process_path: String,
    pid: i32,
    window_title: Option<String>,
}

fn main() -> Result<()> {
    let endpoint = std::env::var("ACTIVITY_AGENT_ENDPOINT")
        .unwrap_or_else(|_| "http://127.0.0.1:8097/gui/activity-ingest".to_string());
    let device_id =
        std::env::var("ACTIVITY_AGENT_DEVICE_ID").unwrap_or_else(|_| "desktop-main".to_string());
    let agent_name =
        std::env::var("ACTIVITY_AGENT_NAME").unwrap_or_else(|_| "live2d-rust-agent".to_string());

    let client = Client::builder().timeout(Duration::from_secs(4)).build()?;
    let mut last_sent: Option<(LastApp, Instant)> = None;
    let sample_interval = Duration::from_secs(8);

    loop {
        let presence = current_presence();
        if let Some((app, title)) = current_foreground_app() {
            let marker = LastApp {
                process_path: app.id.clone(),
                pid: app.pid.unwrap_or_default() as i32,
                window_title: title.clone(),
            };

            let changed = last_sent
                .as_ref()
                .map(|(prev, _)| prev != &marker)
                .unwrap_or(true);
            let sample_due = last_sent
                .as_ref()
                .map(|(_, at)| at.elapsed() >= sample_interval)
                .unwrap_or(true);

            if changed || sample_due {
                let event = ActivityEvent {
                    event_id: Uuid::new_v4().to_string(),
                    ts: Utc::now().to_rfc3339(),
                    device_id: device_id.clone(),
                    agent_name: agent_name.clone(),
                    platform: "windows".to_string(),
                    kind: if changed {
                        "foreground_changed"
                    } else {
                        "activity_sample"
                    }
                    .to_string(),
                    app: app.clone(),
                    window_title: title.clone(),
                    browser: detect_browser_context(&app, title.as_deref()),
                    presence: presence.to_string(),
                    source: "rust-agent".to_string(),
                };

                match client.post(&endpoint).json(&event).send() {
                    Ok(_resp) => {}
                    Err(err) => {
                        eprintln!(
                            "[rust-activity-agent] post failed kind={} app={} err={}",
                            event.kind, event.app.name, err
                        );
                    }
                }
                last_sent = Some((marker, Instant::now()));
            }
        }

        thread::sleep(Duration::from_secs(1));
    }
}

fn current_presence() -> &'static str {
    if is_locked_session() {
        return "locked";
    }
    let mut info = LASTINPUTINFO {
        cbSize: std::mem::size_of::<LASTINPUTINFO>() as u32,
        dwTime: 0,
    };
    let ok = unsafe { GetLastInputInfo(&mut info) };
    if ok == 0 {
        return "active";
    }
    let tick_now = unsafe { GetTickCount() };
    let idle_ms = tick_now.saturating_sub(info.dwTime);
    if idle_ms >= 5 * 60 * 1000 {
        "idle"
    } else {
        "active"
    }
}

fn is_locked_session() -> bool {
    let session_id = unsafe { WTSGetActiveConsoleSessionId() };
    session_id == u32::MAX
}

fn detect_browser_context(app: &ActivityApp, window_title: Option<&str>) -> Option<BrowserContext> {
    let app_name = app.name.to_lowercase();
    let browser_name = if app_name.contains("chrome") {
        Some(("chromium", "Chrome"))
    } else if app_name.contains("msedge") || app_name.contains("edge") {
        Some(("chromium", "Edge"))
    } else if app_name.contains("firefox") {
        Some(("firefox", "Firefox"))
    } else {
        None
    }?;

    let title = window_title
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty());
    let mut domain = title.as_deref().and_then(infer_domain_from_text);
    let url = title.as_deref().and_then(infer_url_from_text);
    let mut source = "window-title".to_string();
    let mut confidence = if domain.is_some() || url.is_some() {
        0.62
    } else {
        0.25
    };

    if url.is_some() {
        source = "window-title-url".to_string();
        confidence = 0.76;
        if domain.is_none() {
            domain = url.as_deref().and_then(extract_domain_from_url);
        }
    }

    Some(BrowserContext {
        family: browser_name.0.to_string(),
        name: browser_name.1.to_string(),
        page_title: title,
        url,
        domain,
        source,
        confidence,
    })
}

fn infer_url_from_text(text: &str) -> Option<String> {
    let raw = text.trim();
    if raw.starts_with("http://") || raw.starts_with("https://") {
        return Some(raw.to_string());
    }
    None
}

fn infer_domain_from_text(text: &str) -> Option<String> {
    let raw = text.trim();
    for token in raw.split(|c: char| c.is_whitespace() || c == '|' || c == '-' || c == '—') {
        let t = token.trim_matches(|c: char| c == '(' || c == ')' || c == '[' || c == ']');
        if t.contains('.') && !t.contains('/') && t.chars().any(|c| c.is_ascii_alphabetic()) {
            return Some(t.trim_end_matches('.').to_string());
        }
    }
    None
}

fn extract_domain_from_url(url: &str) -> Option<String> {
    let no_scheme = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))
        .unwrap_or(url);
    Some(
        no_scheme
            .split('/')
            .next()
            .unwrap_or(no_scheme)
            .split(':')
            .next()
            .unwrap_or(no_scheme)
            .to_string(),
    )
}

fn current_foreground_app() -> Option<(ActivityApp, Option<String>)> {
    let hwnd = unsafe { GetForegroundWindow() };
    if hwnd == 0 as HWND {
        return None;
    }

    let pid = process_id_from_hwnd(hwnd)?;
    let process_path = process_path(pid).unwrap_or_else(|| format!("pid:{pid}"));
    let app_name = std::path::Path::new(&process_path)
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty())
        .map(|name| name.to_string())
        .unwrap_or_else(|| format!("pid-{pid}"));
    let title = window_title(hwnd);

    Some((
        ActivityApp {
            id: process_path,
            name: app_name,
            title: title.clone(),
            pid: Some(pid),
        },
        title,
    ))
}

fn process_id_from_hwnd(hwnd: HWND) -> Option<u32> {
    let mut pid: u32 = 0;
    unsafe { GetWindowThreadProcessId(hwnd, &mut pid) };
    if pid == 0 {
        None
    } else {
        Some(pid)
    }
}

fn process_path(pid: u32) -> Option<String> {
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle == ptr::null_mut() {
        return None;
    }

    let mut size: u32 = 260;
    let mut buffer = vec![0u16; size as usize];
    let ok = unsafe { QueryFullProcessImageNameW(handle, 0, buffer.as_mut_ptr(), &mut size) };
    unsafe { CloseHandle(handle) };
    if ok == 0 || size == 0 {
        return None;
    }
    Some(String::from_utf16_lossy(&buffer[..size as usize]))
}

fn window_title(hwnd: HWND) -> Option<String> {
    let len = unsafe { GetWindowTextLengthW(hwnd) };
    if len <= 0 {
        return None;
    }
    let mut buffer = vec![0u16; (len + 1) as usize];
    let read = unsafe { GetWindowTextW(hwnd, buffer.as_mut_ptr(), len + 1) };
    if read <= 0 {
        return None;
    }
    let title = String::from_utf16_lossy(&buffer[..read as usize])
        .trim()
        .to_string();
    if title.is_empty() {
        None
    } else {
        Some(title)
    }
}

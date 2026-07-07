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
    #[serde(skip_serializing_if = "Option::is_none")]
    sedentary: Option<SedentaryPayload>,
    source: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SedentaryPayload {
    active_minutes: u64,
    window_minutes: u64,
    break_minutes: u64,
    cooldown_minutes: u64,
    boundary_minute: u64,
    rest_streak: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct LastApp {
    process_path: String,
    pid: i32,
    window_title: Option<String>,
}

#[derive(Debug, Clone)]
struct SedentaryTracker {
    window_minutes: u64,
    break_minutes: u64,
    cooldown_minutes: u64,
    active_minutes: u64,
    rest_streak: u64,
    last_sample_minute: Option<u64>,
    last_alert_minute: Option<u64>,
}

#[derive(Debug, Clone)]
struct MinuteActivityTracker {
    current_minute: Option<u64>,
    input_count: u32,
    last_input_tick: Option<u32>,
    active_threshold: u32,
}

impl MinuteActivityTracker {
    fn new(active_threshold: u32) -> Self {
        Self {
            current_minute: None,
            input_count: 0,
            last_input_tick: None,
            active_threshold: active_threshold.max(1),
        }
    }

    fn sample(&mut self, minute: u64, input_tick: Option<u32>) -> Option<(u64, &'static str, u32)> {
        let completed = match self.current_minute {
            None => {
                self.current_minute = Some(minute);
                None
            }
            Some(current) if minute == current => None,
            Some(current) => {
                let count = self.input_count;
                self.current_minute = Some(minute);
                self.input_count = 0;
                let presence = if count >= self.active_threshold {
                    "active"
                } else {
                    "idle"
                };
                Some((current, presence, count))
            }
        };

        if let Some(tick) = input_tick {
            if self
                .last_input_tick
                .map(|last| last != tick)
                .unwrap_or(false)
            {
                self.input_count = self.input_count.saturating_add(1);
            }
            self.last_input_tick = Some(tick);
        }

        completed
    }
}

impl SedentaryTracker {
    fn new(window_minutes: u64, break_minutes: u64, cooldown_minutes: u64) -> Self {
        Self {
            window_minutes: window_minutes.max(1),
            break_minutes: break_minutes.max(1),
            cooldown_minutes: cooldown_minutes.max(1),
            active_minutes: 0,
            rest_streak: 0,
            last_sample_minute: None,
            last_alert_minute: None,
        }
    }

    fn observe(&mut self, minute: u64, presence: &str) -> Option<SedentaryPayload> {
        if self.last_sample_minute == Some(minute) {
            return None;
        }
        if self
            .last_sample_minute
            .map(|last| minute.saturating_sub(last) > 1)
            .unwrap_or(false)
        {
            self.active_minutes = 0;
            self.rest_streak = 0;
        }
        self.last_sample_minute = Some(minute);

        if presence != "active" {
            self.rest_streak = self.rest_streak.saturating_add(1);
            if self.rest_streak >= self.break_minutes {
                self.active_minutes = 0;
                self.last_alert_minute = None;
            }
            return None;
        }

        self.rest_streak = 0;
        self.active_minutes = self.active_minutes.saturating_add(1);
        if self.active_minutes < self.window_minutes {
            return None;
        }
        if self
            .last_alert_minute
            .map(|last| minute.saturating_sub(last) < self.cooldown_minutes)
            .unwrap_or(false)
        {
            return None;
        }

        self.last_alert_minute = Some(minute);
        Some(self.snapshot(minute))
    }

    fn snapshot(&self, minute: u64) -> SedentaryPayload {
        SedentaryPayload {
            active_minutes: self.active_minutes,
            window_minutes: self.window_minutes,
            break_minutes: self.break_minutes,
            cooldown_minutes: self.cooldown_minutes,
            boundary_minute: minute,
            rest_streak: self.rest_streak,
        }
    }
}

fn main() -> Result<()> {
    let endpoint = std::env::var("ACTIVITY_AGENT_ENDPOINT")
        .unwrap_or_else(|_| "http://127.0.0.1:8097/gui/activity-ingest".to_string());
    let access_token = std::env::var("ACTIVITY_AGENT_TOKEN").unwrap_or_default();
    let device_id =
        std::env::var("ACTIVITY_AGENT_DEVICE_ID").unwrap_or_else(|_| "desktop-main".to_string());
    let agent_name =
        std::env::var("ACTIVITY_AGENT_NAME").unwrap_or_else(|_| "live2d-rust-agent".to_string());

    let client = Client::builder().timeout(Duration::from_secs(4)).build()?;
    let mut last_sent: Option<(LastApp, Instant)> = None;
    let sample_interval = Duration::from_secs(8);
    let mut sedentary_tracker = SedentaryTracker::new(
        read_env_u64("ACTIVITY_AGENT_SEDENTARY_WINDOW_MINUTES", 60),
        read_env_u64("ACTIVITY_AGENT_SEDENTARY_BREAK_MINUTES", 5),
        read_env_u64("ACTIVITY_AGENT_SEDENTARY_COOLDOWN_MINUTES", 60),
    );
    let mut minute_activity_tracker = MinuteActivityTracker::new(3);

    loop {
        let presence = current_presence();
        let current_minute = Utc::now().timestamp().max(0) as u64 / 60;
        let completed_activity =
            minute_activity_tracker.sample(current_minute, current_input_tick());
        let sedentary_alert = completed_activity.and_then(|(minute, sedentary_presence, _)| {
            sedentary_tracker.observe(minute, sedentary_presence)
        });
        let sedentary_state = Some(sedentary_tracker.snapshot(current_minute));
        let (app, title) =
            current_foreground_app().unwrap_or_else(|| fallback_activity_context(presence));
        let browser = detect_browser_context(&app, title.as_deref());
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
                browser: browser.clone(),
                presence: presence.to_string(),
                sedentary: sedentary_state,
                source: "rust-agent".to_string(),
            };

            let sent = post_event(&client, &endpoint, &access_token, &event);
            if sent {
                last_sent = Some((marker, Instant::now()));
            }
        }

        if let Some(payload) = sedentary_alert {
            let event = ActivityEvent {
                event_id: Uuid::new_v4().to_string(),
                ts: Utc::now().to_rfc3339(),
                device_id: device_id.clone(),
                agent_name: agent_name.clone(),
                platform: "windows".to_string(),
                kind: "sedentary_alert".to_string(),
                app: app.clone(),
                window_title: title.clone(),
                browser,
                presence: presence.to_string(),
                sedentary: Some(payload),
                source: "rust-agent".to_string(),
            };
            post_event(&client, &endpoint, &access_token, &event);
        }

        thread::sleep(Duration::from_secs(1));
    }
}

fn read_env_u64(name: &str, default_value: u64) -> u64 {
    std::env::var(name)
        .ok()
        .and_then(|v| v.trim().parse::<u64>().ok())
        .filter(|v| *v > 0)
        .unwrap_or(default_value)
}

fn post_event(client: &Client, endpoint: &str, access_token: &str, event: &ActivityEvent) -> bool {
    let mut request = client.post(endpoint).json(event);
    if !access_token.is_empty() {
        request = request.header("X-GUI-Token", access_token);
    }
    match request.send() {
        Ok(resp) => {
            if resp.status().is_success() {
                true
            } else {
                let status = resp.status();
                let body = resp.text().unwrap_or_default();
                eprintln!(
                    "[rust-activity-agent] post rejected kind={} app={} status={} body={}",
                    event.kind, event.app.name, status, body
                );
                false
            }
        }
        Err(err) => {
            eprintln!(
                "[rust-activity-agent] post failed kind={} app={} err={}",
                event.kind, event.app.name, err
            );
            false
        }
    }
}

fn current_presence() -> &'static str {
    if is_locked_session() {
        return "locked";
    }
    let idle_ms = match current_idle_ms() {
        Some(v) => v,
        None => return "active",
    };
    if idle_ms >= 5 * 60 * 1000 {
        "idle"
    } else {
        "active"
    }
}

fn fallback_activity_context(presence: &'static str) -> (ActivityApp, Option<String>) {
    (
        ActivityApp {
            id: format!("system:{}", presence),
            name: "System".to_string(),
            title: None,
            pid: None,
        },
        Some(format!("No foreground window ({})", presence)),
    )
}

fn current_idle_ms() -> Option<u32> {
    current_input_tick().map(|last_tick| {
        let tick_now = unsafe { GetTickCount() };
        tick_now.saturating_sub(last_tick)
    })
}

fn current_input_tick() -> Option<u32> {
    let mut info = LASTINPUTINFO {
        cbSize: std::mem::size_of::<LASTINPUTINFO>() as u32,
        dwTime: 0,
    };
    let ok = unsafe { GetLastInputInfo(&mut info) };
    if ok == 0 {
        return None;
    }
    Some(info.dwTime)
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sedentary_tracker_alerts_after_active_window() {
        let mut tracker = SedentaryTracker::new(3, 2, 3);

        assert!(tracker.observe(1, "active").is_none());
        assert!(tracker.observe(2, "active").is_none());
        let alert = tracker
            .observe(3, "active")
            .expect("third active minute alerts");

        assert_eq!(alert.active_minutes, 3);
        assert_eq!(alert.window_minutes, 3);
        assert_eq!(alert.break_minutes, 2);
    }

    #[test]
    fn sedentary_tracker_requires_consecutive_rest_to_reset() {
        let mut tracker = SedentaryTracker::new(3, 2, 3);

        assert!(tracker.observe(1, "active").is_none());
        assert!(tracker.observe(2, "idle").is_none());
        assert!(tracker.observe(3, "active").is_none());
        let alert = tracker
            .observe(4, "active")
            .expect("short rest does not reset");

        assert_eq!(alert.active_minutes, 3);
    }

    #[test]
    fn sedentary_tracker_snapshot_reports_current_state_without_alert() {
        let mut tracker = SedentaryTracker::new(60, 5, 60);

        assert!(tracker.observe(1, "active").is_none());
        assert!(tracker.observe(2, "idle").is_none());
        let snapshot = tracker.snapshot(2);

        assert_eq!(snapshot.active_minutes, 1);
        assert_eq!(snapshot.window_minutes, 60);
        assert_eq!(snapshot.break_minutes, 5);
        assert_eq!(snapshot.cooldown_minutes, 60);
        assert_eq!(snapshot.boundary_minute, 2);
        assert_eq!(snapshot.rest_streak, 1);
    }

    #[test]
    fn sedentary_tracker_resets_after_valid_break() {
        let mut tracker = SedentaryTracker::new(3, 2, 3);

        assert!(tracker.observe(1, "active").is_none());
        assert!(tracker.observe(2, "idle").is_none());
        assert!(tracker.observe(3, "idle").is_none());
        assert!(tracker.observe(4, "active").is_none());
        assert!(tracker.observe(5, "active").is_none());
        let alert = tracker
            .observe(6, "active")
            .expect("new window alerts after break");

        assert_eq!(alert.active_minutes, 3);
    }

    #[test]
    fn sedentary_tracker_dedupes_same_minute_and_cooldown() {
        let mut tracker = SedentaryTracker::new(2, 2, 3);

        assert!(tracker.observe(1, "active").is_none());
        assert!(tracker.observe(2, "active").is_some());
        assert!(tracker.observe(2, "active").is_none());
        assert!(tracker.observe(3, "active").is_none());
        assert!(tracker.observe(4, "active").is_none());
        assert!(tracker.observe(5, "active").is_some());
    }

    #[test]
    fn fallback_activity_context_reports_system_presence() {
        let (app, title) = fallback_activity_context("locked");

        assert_eq!(app.id, "system:locked");
        assert_eq!(app.name, "System");
        assert_eq!(title.as_deref(), Some("No foreground window (locked)"));
    }

    #[test]
    fn minute_activity_tracker_counts_input_changes_per_completed_minute() {
        let mut tracker = MinuteActivityTracker::new(3);

        assert!(tracker.sample(10, Some(100)).is_none());
        assert!(tracker.sample(10, Some(101)).is_none());
        assert!(tracker.sample(10, Some(102)).is_none());
        assert!(tracker.sample(10, Some(103)).is_none());

        assert_eq!(tracker.sample(11, Some(104)), Some((10, "active", 3)));
    }

    #[test]
    fn minute_activity_tracker_marks_sparse_input_as_idle() {
        let mut tracker = MinuteActivityTracker::new(3);

        assert!(tracker.sample(20, Some(200)).is_none());
        assert!(tracker.sample(20, Some(201)).is_none());

        assert_eq!(tracker.sample(21, Some(201)), Some((20, "idle", 1)));
    }

    #[test]
    fn idle_sample_does_not_increase_active_minutes() {
        let mut tracker = SedentaryTracker::new(60, 5, 60);

        tracker.observe(1, "active");
        tracker.observe(2, "active");
        tracker.observe(3, "idle");

        let snapshot = tracker.snapshot(3);
        assert_eq!(snapshot.active_minutes, 2);
        assert_eq!(snapshot.rest_streak, 1);
    }
}

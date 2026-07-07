from services.chat_support.gateway_sender import split_gateway_text_parts


def test_split_gateway_text_parts_preserves_mail_list_rows():
    text = (
        "最近邮件：\n"
        "1. 测试 | 李曜同 | 2026-06-26T05:37:37Z | 未读\n"
        "   ID: msg_i36yfQbcmMVELVfbxrCa19243Sw2f-rNxiGPGaQ1fCLjQ\n"
        "   摘要: 你好\n"
        "2. Agent Mail 接入成功 | Agent Mail 团队 | 2026-06-26T05:35:35Z | 未读\n"
        "   ID: msg_FGFGUfN8V6GINln9ZNdTIhOt83F4ks9I-uTkBrSNDY9OOQ\n"
        "   摘要: Agent Mail 接入成功 已为 DESKTOP-93RTOGL 接入 Agent Mail，现在可以在 DESKTOP-93RTOGL 中收发邮件了。"
    )

    parts = split_gateway_text_parts(text)

    assert len(parts) >= 4
    assert all(len(part) <= 260 for part in parts)
    assert any(part.startswith("1. 测试") for part in parts)
    assert any(part.startswith("ID: msg_i36") for part in parts)


def test_split_gateway_text_parts_does_not_rejoin_large_tail():
    lines = ["最近邮件："]
    for idx in range(1, 8):
        lines.extend(
            [
                f"{idx}. 测试邮件标题 {idx} | sender{idx}@example.com | 2026-06-26T05:37:37Z | 未读",
                f"   ID: msg_{idx:02d}_abcdefghijklmnopqrstuvwxyz0123456789",
                "   摘要: " + ("这是一段用于验证 QQ 分段不会重新合成长尾巴的摘要。" * 4),
            ]
        )
    text = "\n".join(lines)

    parts = split_gateway_text_parts(text)

    assert len(parts) > 8
    assert all(len(part) <= 260 for part in parts)
    assert any("msg_07_" in part for part in parts)

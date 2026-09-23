"""(선택) Claude API를 이용한 회의 요약. ANTHROPIC_API_KEY가 있을 때만 사용된다.

규칙 기반 요약(analysis.summarize_meeting)이 기본이며, 이 모듈은 더 자연스러운
주요내용·핵심안건 요약이 필요할 때 보조적으로 쓴다.
"""
from __future__ import annotations

import json
from typing import Any

from .config import Settings
from .store import Store

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overview": {"type": "string", "description": "회의 전체 요약(3~4문장)"},
        "key_agendas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "agenda": {"type": "string"},
                    "summary": {"type": "string"},
                    "outcome": {"type": "string", "description": "의결·보류·계속심사 등 처리 결과, 불명확하면 빈 문자열"},
                },
                "required": ["agenda", "summary", "outcome"],
                "additionalProperties": False,
            },
        },
        "key_issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "raised_by": {"type": "array", "items": {"type": "string"}},
                    "government_position": {"type": "string"},
                },
                "required": ["issue", "raised_by", "government_position"],
                "additionalProperties": False,
            },
        },
        "commitments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "who": {"type": "string"},
                    "what": {"type": "string"},
                },
                "required": ["who", "what"],
                "additionalProperties": False,
            },
        },
        "audit_followups": {
            "type": "array", "items": {"type": "string"},
            "description": "국정감사에서 추가 확인·추궁할 만한 사항",
        },
    },
    "required": ["overview", "key_agendas", "key_issues", "commitments", "audit_followups"],
    "additionalProperties": False,
}

SYSTEM = (
    "당신은 국회 농림축산식품해양수산위원회 회의록을 분석해 국정감사 준비 자료를 만드는 "
    "입법 보좌 전문가입니다. 회의록에 실제로 있는 내용만 근거로 요약하고, 발언자 이름과 "
    "직위를 정확히 유지하세요. 회의록에 없는 사실은 추정하지 마세요."
)


def llm_summarize(store: Store, meeting_id: str, settings: Settings) -> dict[str, Any]:
    import anthropic

    m = store.meeting(meeting_id)
    if not m:
        raise KeyError(meeting_id)
    utts = store.utterances(meeting_id=meeting_id)
    if not utts:
        raise ValueError("회의록 본문이 없어 LLM 요약을 할 수 없습니다.")
    transcript = "\n".join(f"[{u['speaker_role']} {u['speaker_name']}] {u['text']}" for u in utts)
    prompt = (
        f"회의: {m['title']} ({m['date']})\n안건: {', '.join(m['agendas']) or '미상'}\n\n"
        f"<회의록>\n{transcript}\n</회의록>\n\n"
        "위 회의록을 분석해 주요내용, 핵심안건별 요약, 쟁점별 제기 위원과 정부 입장, "
        "정부측 이행약속, 국정감사 후속 점검사항을 정리하세요."
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=settings.llm_model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
        # 안전 분류기에 의한 거절 시 서버측 대체 모델로 자동 재시도
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("모델이 요청을 처리하지 않았습니다(refusal).")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("요약이 출력 한도에서 잘렸습니다.")
    text = next(b.text for b in response.content if b.type == "text")
    result = json.loads(text)
    result["method"] = f"llm:{response.model}"
    store.save_summary(meeting_id, "llm", result)
    return result

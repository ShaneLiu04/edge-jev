"""TypeSafe System One 线协议共享编解码（/v1/systemone 请求/响应互转）。
kev HTTP 后端与 FastAPI 端点（T006）共用，保证线格式单源。"""

import json

from jev.errors import JevProtocolError
from jev.kernel.cerebellum.base import (
    Answer,
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneResponse,
    choice_confidence,
    noul_confidence,
    score_confidence,
)

_MAX_OPTIONS = 255  # TypeSafe 单问选项上限（kev 参照同款）


def encode_request(
    model: str, state: str, questions: dict[str, Question]
) -> dict:
    """Question dataclass → TypeSafe 请求线格式（三型统一 criteria 字段）。"""
    wire_questions: dict[str, dict] = {}
    for question_id, question in questions.items():
        if isinstance(question, ChoiceQuestion):
            wire_questions[question_id] = {
                "type": "choice",
                "criteria": dict(question.choices),
                "instructions": question.instructions,
            }
        elif isinstance(question, NoulQuestion):
            wire_question: dict = {
                "type": "noul",
                "instructions": question.instructions,
            }
            if question.criteria is not None:
                wire_question["criteria"] = dict(question.criteria)
            wire_questions[question_id] = wire_question
        elif isinstance(question, ScoreQuestion):
            wire_questions[question_id] = {
                "type": "score",
                "criteria": list(question.criteria),
                "instructions": question.instructions,
            }
        else:
            raise JevProtocolError(f"未知问题类型: {type(question).__name__}")
    return {"model": model, "state": state, "questions": wire_questions}


def decode_request(payload: object) -> tuple[str, str, dict[str, Question]]:
    """TypeSafe 请求线格式 → (model, state 文本, Question 集)；结构非法抛可定位 JevProtocolError。

    state 为 dict/list 时序列化为紧凑 JSON 字符串（非结构化状态对象直通小脑）。
    """
    if not isinstance(payload, dict):
        raise JevProtocolError(f"请求须为 JSON 对象，实际: {type(payload).__name__}")

    model = payload.get("model")
    if not isinstance(model, str) or not model:
        raise JevProtocolError(f"model 必选且须为非空字符串，实际: {model!r}")

    state = payload.get("state")
    if isinstance(state, str):
        state_text = state
    elif isinstance(state, (dict, list)):
        state_text = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    else:
        raise JevProtocolError(
            f"state 必选且须为 string 或对象，实际: {type(state).__name__}"
        )

    questions_raw = payload.get("questions")
    if not isinstance(questions_raw, dict) or not questions_raw:
        raise JevProtocolError("questions 必选、须为对象且至少包含 1 问")

    questions = {
        question_id: _decode_question(question_id, spec)
        for question_id, spec in questions_raw.items()
    }
    return model, state_text, questions


def _decode_question(question_id: str, spec: object) -> Question:
    if not isinstance(spec, dict):
        raise JevProtocolError(
            f"questions.{question_id} 须为对象，实际: {type(spec).__name__}"
        )
    instructions = spec.get("instructions")
    if not isinstance(instructions, str) or not instructions:
        raise JevProtocolError(
            f"questions.{question_id}.instructions 必选且须为非空字符串"
        )

    question_type = spec.get("type")
    if question_type == "choice":
        criteria = spec.get("criteria")
        if not isinstance(criteria, dict) or not criteria:
            raise JevProtocolError(
                f"questions.{question_id}.criteria 必选（choice 问）"
                "且须为非空 {label: 描述|null} 对象"
            )
        if len(criteria) > _MAX_OPTIONS:
            raise JevProtocolError(
                f"questions.{question_id}.criteria 选项数超上限（>{_MAX_OPTIONS}）"
            )
        choices: dict[str, str | None] = {}
        for label, desc in criteria.items():
            if desc is None:
                choices[str(label)] = None
            elif isinstance(desc, (str, int, float, bool)):
                choices[str(label)] = str(desc)
            else:
                raise JevProtocolError(
                    f"questions.{question_id}.criteria[{label}] 选项描述须为字符串或 null"
                )
        return ChoiceQuestion(choices=choices, instructions=instructions)
    if question_type == "noul":
        raw_criteria = spec.get("criteria")
        if raw_criteria is None:
            noul_criteria = None
        elif isinstance(raw_criteria, dict):
            noul_criteria = {
                str(key): (None if value is None else str(value))
                for key, value in raw_criteria.items()
            }
        else:
            raise JevProtocolError(
                f"questions.{question_id}.criteria 须为 {{true, false}} 选项描述对象"
            )
        return NoulQuestion(instructions=instructions, criteria=noul_criteria)
    if question_type == "score":
        criteria = spec.get("criteria")
        if (
            not isinstance(criteria, list)
            or not criteria
            or not all(isinstance(level, str) for level in criteria)
        ):
            raise JevProtocolError(
                f"questions.{question_id}.criteria 必选（score 问）且须为非空有序字符串数组"
            )
        if len(criteria) > _MAX_OPTIONS:
            raise JevProtocolError(
                f"questions.{question_id}.criteria 等级数超上限（>{_MAX_OPTIONS}）"
            )
        return ScoreQuestion(criteria=list(criteria), instructions=instructions)
    raise JevProtocolError(
        f"questions.{question_id}.type 非法: {question_type!r}（合法: choice/noul/score）"
    )


def encode_response(response: SystemOneResponse, model: str) -> dict:
    """SystemOneResponse → TypeSafe 响应线格式（与 decode_response 互逆）。"""
    answers: dict[str, dict] = {}
    for question_id, answer in response.answers.items():
        if isinstance(answer, ChoiceAnswer):
            answers[question_id] = {
                "type": "choice",
                "choice": answer.choice,
                "confidence": answer.confidence,
                "probabilities": dict(answer.probabilities),
            }
        elif isinstance(answer, NoulAnswer):
            answers[question_id] = {"type": "noul", "noul": answer.noul}
        elif isinstance(answer, ScoreAnswer):
            answers[question_id] = {
                "type": "score",
                "score": answer.score,
                "confidence": answer.confidence,
                "legend": dict(answer.legend),
                "probabilities": dict(answer.probabilities),
            }
        else:
            raise JevProtocolError(f"未知答案类型: {type(answer).__name__}")
    payload: dict = {
        "model": model,
        "answers": answers,
        "usage": {"input_tokens": response.input_tokens},
    }
    if response.truncated:
        payload["truncated"] = True
    return payload


def decode_response(payload: dict) -> SystemOneResponse:
    """TypeSafe 响应线格式 → SystemOneResponse；结构非法抛 JevProtocolError（可定位）。"""
    if not isinstance(payload, dict):
        raise JevProtocolError(f"响应须为 JSON 对象，实际: {type(payload).__name__}")

    answers_raw = payload.get("answers")
    if not isinstance(answers_raw, dict):
        raise JevProtocolError("响应缺少 answers（或非对象）")

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise JevProtocolError("响应缺少 usage（或非对象）")
    input_tokens = usage.get("input_tokens")
    if not isinstance(input_tokens, int) or isinstance(input_tokens, bool):
        raise JevProtocolError(f"usage.input_tokens 须为整数，实际: {input_tokens!r}")

    answers: dict[str, Answer] = {}
    for question_id, answer_raw in answers_raw.items():
        answers[question_id] = _decode_answer(question_id, answer_raw)

    return SystemOneResponse(
        answers=answers,
        input_tokens=input_tokens,
        truncated=bool(payload.get("truncated", False)),
    )


def _decode_answer(question_id: str, answer_raw: object) -> Answer:
    if not isinstance(answer_raw, dict):
        raise JevProtocolError(f"answers.{question_id} 须为对象，实际: {type(answer_raw).__name__}")
    answer_type = answer_raw.get("type")

    if answer_type == "choice":
        choice = answer_raw.get("choice")
        probabilities = answer_raw.get("probabilities")
        if not isinstance(choice, str):
            raise JevProtocolError(f"answers.{question_id} 缺少合法 choice")
        if not isinstance(probabilities, dict):
            raise JevProtocolError(f"answers.{question_id} 缺少 probabilities（或非对象）")
        return ChoiceAnswer(
            choice=choice,
            confidence=choice_confidence(probabilities),
            probabilities=dict(probabilities),
        )

    if answer_type == "noul":
        noul = answer_raw.get("noul")
        if not isinstance(noul, (int, float)) or isinstance(noul, bool):
            raise JevProtocolError(f"answers.{question_id} 缺少合法 noul 数值")
        noul_value = float(noul)
        return NoulAnswer(
            noul=noul_value,
            confidence=noul_confidence(noul_value),
        )

    if answer_type == "score":
        score = answer_raw.get("score")
        legend = answer_raw.get("legend")
        probabilities = answer_raw.get("probabilities")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise JevProtocolError(
                f"answers.{question_id}.score 须为数值（期望等级 E[level]）"
            )
        if not isinstance(legend, dict) or not legend:
            raise JevProtocolError(
                f"answers.{question_id} 缺少 legend（等级索引→等级文本）"
            )
        if not isinstance(probabilities, dict) or not probabilities:
            raise JevProtocolError(
                f"answers.{question_id} 缺少 probabilities（等级索引→概率）"
            )
        probs = {str(k): float(v) for k, v in probabilities.items()}
        return ScoreAnswer(
            score=float(score),
            confidence=score_confidence(probs),
            legend={str(k): str(v) for k, v in legend.items()},
            probabilities=probs,
        )

    raise JevProtocolError(
        f"answers.{question_id} 的 type 非法: {answer_type!r}（合法: choice/noul/score）"
    )

#!/usr/bin/env python3
import copy
import json
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


CASES = [
    {"id": "moon_normal", "group": "curated", "question": "为什么月亮好像跟着我走？", "feedback": "normal"},
    {"id": "sky_normal", "group": "curated", "question": "为什么天空是蓝色的？", "feedback": "normal"},
    {"id": "bird_uncertain", "group": "curated", "question": "小鸟飞走以后，它的妈妈会想它吗？", "feedback": "normal"},
    {"id": "moon_simpler", "group": "curated", "question": "为什么月亮好像跟着我走？", "feedback": "simpler"},
    {"id": "sky_example", "group": "curated", "question": "为什么天空是蓝色的？", "feedback": "example"},
    {
        "id": "moon_teachback_correct",
        "group": "curated",
        "question": "因为月亮离我们很远，我走几步时它看起来位置变化很小。",
        "originalQuestion": "为什么月亮好像跟着我走？",
        "feedback": "teachback",
    },
    {
        "id": "moon_teachback_incorrect",
        "group": "curated",
        "question": "因为月亮真的喜欢我，所以一直追着我。",
        "originalQuestion": "为什么月亮好像跟着我走？",
        "feedback": "teachback",
    },
    {"id": "rain_normal", "group": "generic", "question": "为什么会下雨？", "feedback": "normal"},
    {"id": "rain_simpler", "group": "generic", "question": "为什么会下雨？", "feedback": "simpler"},
    {"id": "shadow_example", "group": "generic", "question": "影子为什么会跟着我？", "feedback": "example"},
    {"id": "dinosaur_age7", "group": "generic", "question": "恐龙为什么不见了？", "feedback": "normal", "age": 7},
    {"id": "bubble_age7", "group": "generic", "question": "肥皂泡为什么有彩色？", "feedback": "normal", "age": 7},
    {"id": "dinosaur_uncertain", "group": "generic", "question": "恐龙会不会觉得孤单？", "feedback": "normal"},
    {"id": "imagination_boundary", "group": "boundary", "question": "如果我是会飞的小恐龙，会发生什么？", "feedback": "normal"},
]


def contains_any(text, terms):
    return any(term in text for term in terms)


def contains_bubble_reflection_mechanism(text):
    surface_reflection = "反射" in text and (
        ("里面" in text and "外面" in text)
        or "两面" in text
        or "前后" in text
    )
    return surface_reflection or contains_any(text, ["两面反射", "来回反射", "前后反射", "内外都反射"])


class RegressionHelperTests(unittest.TestCase):
    def test_bubble_reflection_detector_allows_intervening_surface_noun(self):
        self.assertTrue(contains_bubble_reflection_mechanism("光在皮的里面和外面都反射。"))


def within_language_contract(item):
    evidence = ((item.get("quality") or {}).get("languageEvidence") or {})
    sentence_chars = evidence.get("sentenceChars") or []
    return (
        evidence.get("answerChars", 10**9) <= evidence.get("maxAnswerChars", -1)
        and evidence.get("sentenceCount", 10**9) <= evidence.get("maxSentences", -1)
        and all(length <= evidence.get("maxSentenceChars", -1) for length in sentence_chars)
        and not evidence.get("adultTerms")
        and len(evidence.get("conceptLabels") or []) <= evidence.get("conceptLimit", -1)
    )


def main():
    base_profile = app.seed_db()["profiles"][app.DEFAULT_CHILD_ID]
    results = []
    for case in CASES:
        profile = copy.deepcopy(base_profile)
        profile["age"] = case.get("age", profile.get("age", 5))
        started = time.perf_counter()
        answer, workflow = app.run_child_alignment_workflow(
            profile,
            case["question"],
            case["feedback"],
            [],
            "ask",
            case.get("originalQuestion", ""),
        )
        plan = workflow.get("questionPlan") or {}
        validation = plan.get("planValidation") or {}
        semantic_review = plan.get("planSemanticReview") or {}
        reference_checks = validation.get("referenceChecks") or {}
        results.append({
            "id": case["id"],
            "group": case["group"],
            "age": profile["age"],
            "question": case["question"],
            "feedback": case["feedback"],
            "displayText": answer.get("displayText", ""),
            "followUp": answer.get("followUp", ""),
            "safeExperiment": answer.get("safeExperiment", ""),
            "truthKernel": answer.get("truthKernel", ""),
            "epistemicStatus": answer.get("epistemicStatus", ""),
            "answerSource": workflow.get("answerSource"),
            "deliveryDecision": workflow.get("deliveryDecision"),
            "teachbackAssessment": workflow.get("teachbackAssessment"),
            "alignmentContract": workflow.get("alignmentContract"),
            "questionPlan": {
                "planSource": plan.get("planSource"),
                "planStatus": plan.get("planStatus"),
                "epistemicStatus": plan.get("epistemicStatus"),
                "requiredIdeaGroups": plan.get("requiredIdeaGroups"),
                "requiredIdeaGroupsByMode": plan.get("requiredIdeaGroupsByMode", {}),
                "causalChainByMode": plan.get("causalChainByMode", {}),
                "conceptLabels": plan.get("conceptLabels"),
                "analogyPolicy": plan.get("analogyPolicy"),
                "validatedExampleMarkers": plan.get("validatedExampleMarkers"),
                "validatedExampleType": plan.get("validatedExampleType"),
                "validationPassed": validation.get("valid"),
                "validationViolations": validation.get("violations", []),
                "referenceChecksPassed": all(bool(check.get("passed")) for check in reference_checks.values()) if reference_checks else False,
                "semanticReviewPassed": semantic_review.get("passed"),
                "semanticReviewSource": semantic_review.get("reviewSource"),
                "semanticViolations": semantic_review.get("violations", []),
                "minimalTruthSufficient": semantic_review.get("minimalTruthSufficient"),
                "materialOmission": semantic_review.get("materialOmission"),
                "optionalAdvancedDetails": semantic_review.get("optionalAdvancedDetails", []),
                "ideaGroupSource": plan.get("ideaGroupSource", "planner_or_curated"),
                "childExpressionSource": plan.get("childExpressionSource", "planner"),
                "factScaffoldSource": plan.get("factScaffoldSource", ""),
                "referenceRepairProvided": semantic_review.get("referenceRepairProvided", False),
                "planRepairCount": plan.get("planRepairCount", 0),
            },
            "quality": workflow.get("quality"),
            "latencySeconds": round(time.perf_counter() - started, 3),
        })

    indexed = {item["id"]: item for item in results}
    generic = [item for item in results if item["group"] == "generic"]
    simpler = indexed["moon_simpler"]
    simpler_quality = simpler.get("quality") or {}
    simpler_contract = simpler.get("alignmentContract") or {}
    normal_contract = indexed["moon_normal"].get("alignmentContract") or {}
    diagnostics = {
        "all_native_real": all(item["answerSource"] == "real" for item in results),
        "quality_fallback_count": sum(1 for item in results if item["answerSource"] == "quality_fallback"),
    }
    checks = {
        "all_deliveries_validated": all(item["answerSource"] == "real" for item in results),
        "all_delivery_decisions_validated": all(bool((item.get("deliveryDecision") or {}).get("deliveryValidated")) for item in results),
        "all_quality_passed": all(bool((item.get("quality") or {}).get("passed")) for item in results),
        "all_plans_ready": all((item.get("questionPlan") or {}).get("planStatus") == "ready" for item in results),
        "all_plans_locally_validated": all(bool((item.get("questionPlan") or {}).get("validationPassed")) for item in results),
        "all_reference_answers_validated": all(bool((item.get("questionPlan") or {}).get("referenceChecksPassed")) for item in results),
        "all_plans_semantically_reviewed": all(bool((item.get("questionPlan") or {}).get("semanticReviewPassed")) for item in results),
        "all_semantic_reviews_use_minimal_truth": all(bool((item.get("questionPlan") or {}).get("minimalTruthSufficient")) and not bool((item.get("questionPlan") or {}).get("materialOmission")) for item in results),
        "generic_questions_used_planner": all(
            str((item.get("questionPlan") or {}).get("planSource", "")).startswith("parent_analysis_compiled")
            for item in generic
        ),
        "generic_answers_are_native_real": all(item["answerSource"] == "real" for item in generic),
        "generic_atomic_coverage_complete": all(bool(((item.get("quality") or {}).get("atomicCoverage") or {}).get("allPresent")) for item in generic),
        "generic_causal_chains_complete": all(bool(((item.get("quality") or {}).get("causalCoverage") or {}).get("ordered", True)) for item in generic),
        "generic_analogy_policy_enforced": all((item.get("quality") or {}).get("scores", {}).get("analogyBounded") == 1.0 for item in generic),
        "no_scientific_personification": all(not app.scientific_metaphor_hits(item["displayText"]) for item in results),
        "moon_truth_retained": contains_any(indexed["moon_normal"]["displayText"], ["很远", "太远", "离得太远", "远得多"]) and contains_any(indexed["moon_normal"]["displayText"], ["位置变化很小", "方向变化很小", "几乎没变", "看起来没变", "差不多的位置"]),
        "sky_truth_retained": contains_any(indexed["sky_normal"]["displayText"], ["阳光", "太阳光", "光里"]) and contains_any(indexed["sky_normal"]["displayText"], ["蓝光", "蓝色光", "蓝色的光"]) and contains_any(indexed["sky_normal"]["displayText"], ["散开", "四周"]),
        "bird_uncertainty_explicit": contains_any(indexed["bird_uncertain"]["displayText"], list(app.UNCERTAINTY_MARKERS)) and contains_any(indexed["bird_uncertain"]["displayText"], ["照顾", "寻找", "去找", "找它", "呼唤", "喂"]),
        "simpler_contract_is_stricter": simpler_contract.get("maxSentenceChars", 99) < normal_contract.get("maxSentenceChars", 0) and simpler_contract.get("maxAnswerChars", 999) < normal_contract.get("maxAnswerChars", 0),
        "simpler_meets_child_complexity_budget": within_language_contract(simpler),
        "simpler_keeps_all_fact_atoms": bool((simpler_quality.get("atomicCoverage") or {}).get("allPresent")),
        "generic_simpler_uses_mode_fact_budget": bool((indexed["rain_simpler"]["questionPlan"].get("requiredIdeaGroupsByMode") or {}).get("simpler")),
        "example_uses_vetted_content": contains_any(indexed["sky_example"]["displayText"], ["抬头看四周", "散到了各处"]),
        "generic_example_uses_vetted_content": any(marker in indexed["shadow_example"]["displayText"] for marker in (indexed["shadow_example"]["questionPlan"].get("validatedExampleMarkers") or [])),
        "generic_example_is_typed": indexed["shadow_example"]["questionPlan"].get("validatedExampleType") in {"observation", "analogy"},
        "correct_teachback_acknowledged": contains_any(indexed["moon_teachback_correct"]["displayText"], list(app.TEACHBACK_ACK_MARKERS)),
        "correct_teachback_evidence_reviewed": (
            bool((indexed["moon_teachback_correct"].get("teachbackAssessment") or {}).get("correct"))
            and bool((indexed["moon_teachback_correct"].get("teachbackAssessment") or {}).get("reviewed"))
            and bool((indexed["moon_teachback_correct"].get("teachbackAssessment") or {}).get("reviewPassed"))
            and (indexed["moon_teachback_correct"].get("teachbackAssessment") or {}).get("source") == "local+parent_analysis"
            and bool((indexed["moon_teachback_correct"].get("teachbackAssessment") or {}).get("coveragePassed"))
            and not bool((indexed["moon_teachback_correct"].get("teachbackAssessment") or {}).get("contradiction"))
            and bool((indexed["moon_teachback_correct"].get("teachbackAssessment") or {}).get("causalOrderCorrect"))
        ),
        "incorrect_teachback_corrected": contains_any(indexed["moon_teachback_incorrect"]["displayText"], ["不是月亮在追", "没有在追", "不是真的跟着"]),
        "incorrect_teachback_rejected_as_mastery": (
            not bool((indexed["moon_teachback_incorrect"].get("teachbackAssessment") or {}).get("correct"))
            and (indexed["moon_teachback_incorrect"].get("teachbackAssessment") or {}).get("outcome") == "contradicted"
            and bool((indexed["moon_teachback_incorrect"].get("teachbackAssessment") or {}).get("contradiction"))
        ),
        "dinosaur_extinction_evidence_bounded": indexed["dinosaur_age7"]["epistemicStatus"] == "evidence_based" and contains_any(indexed["dinosaur_age7"]["displayText"], list(app.EVIDENCE_BASED_MARKERS)),
        "dinosaur_causal_bridge_retained": contains_any(indexed["dinosaur_age7"]["displayText"], ["灰尘", "天变暗", "挡住阳光"]) and contains_any(indexed["dinosaur_age7"]["displayText"], ["植物", "食物"]),
        "bubble_child_safe_mechanism_retained": contains_any(indexed["bubble_age7"]["displayText"], ["泡泡皮", "薄膜", "膜很薄"]) and contains_bubble_reflection_mechanism(indexed["bubble_age7"]["displayText"]) and contains_any(indexed["bubble_age7"]["displayText"], ["颜色变亮", "颜色就变亮", "不同颜色", "不同的颜色", "彩色"]),
        "generic_subjective_unknown_explicit": indexed["dinosaur_uncertain"]["epistemicStatus"] == "subjective_unknown" and contains_any(indexed["dinosaur_uncertain"]["displayText"], list(app.SUBJECTIVE_UNKNOWN_MARKERS)),
        "imagination_boundary_explicit": contains_any(indexed["imagination_boundary"]["displayText"], ["想象", "故事", "假装", "不是真的"]),
        "age_seven_contract_applied": indexed["dinosaur_age7"]["alignmentContract"].get("maxSentenceChars") == 24 and indexed["dinosaur_age7"]["alignmentContract"].get("conceptLimit") == 2,
        "no_none_strings": all("none" not in " ".join([item["displayText"], item["followUp"], item["safeExperiment"]]).lower() for item in results),
    }
    observations = {
        "moonNormalChars": len(indexed["moon_normal"]["displayText"]),
        "moonSimplerChars": len(indexed["moon_simpler"]["displayText"]),
        "rawLengthComparisonIsInformational": True,
        "totalLatencySeconds": round(sum(item["latencySeconds"] for item in results), 3),
    }
    print(json.dumps({"results": results, "checks": checks, "diagnostics": diagnostics, "observations": observations, "passed": all(checks.values())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

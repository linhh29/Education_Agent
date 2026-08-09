import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


def candidate(text, truth="", epistemic="fact", analogy=""):
    return app.normalize_ai_response({
        "displayText": text,
        "speakText": text,
        "followUp": "",
        "safeExperiment": "",
        "avatarState": "speaking",
        "introducedConcepts": [],
        "knowledgeCardUpdates": [],
        "safetyAction": "none",
        "needsParent": False,
        "answerType": "factual",
        "truthKernel": truth,
        "epistemicStatus": epistemic,
        "analogy": analogy,
    })


def reviewed_correct_teachback():
    return {
        "type": "mastery",
        "correct": True,
        "outcome": "mastered",
        "localPassed": True,
        "reviewed": True,
        "reviewPassed": True,
        "assessmentAvailable": True,
        "source": "local+parent_analysis",
    }


def contradicted_teachback(reviewed=False):
    return {
        "type": "mastery",
        "correct": False,
        "outcome": "contradicted",
        "localPassed": False,
        "reviewed": reviewed,
        "reviewPassed": False,
        "assessmentAvailable": True,
        "source": "local+parent_analysis" if reviewed else "local_teachback_gate",
    }


def unreviewed_positive_teachback():
    return {
        "type": "mastery",
        "correct": True,
        "outcome": "candidate_mastery",
        "localPassed": True,
        "reviewed": False,
        "reviewPassed": False,
        "assessmentAvailable": True,
        "source": "local_teachback_gate",
    }


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.profile = app.seed_db()["profiles"][app.DEFAULT_CHILD_ID]

    def review(self, question, answer):
        plan = app.heuristic_question_plan(question)
        contract = app.child_level_alignment(self.profile)
        return app.local_quality_review(answer, plan, contract)

    def rain_phrase_review(self, text):
        plan = {
            "planStatus": "ready",
            "feedbackMode": "normal",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["水汽"], ["小水滴"], ["落下来", "成雨"]],
            "conceptLabels": ["下雨"],
            "analogyPolicy": "omit",
        }
        return app.local_quality_review(candidate(text), plan, app.child_level_alignment(self.profile))

    def test_moon_answer_keeps_truth_core(self):
        answer = candidate("月亮离我们很远。你走几步时，它看起来位置变化很小。")
        self.assertTrue(self.review("为什么月亮好像跟着我走？", answer)["passed"])

    def test_fact_gate_accepts_natural_particle_and_insertion_rephrasing(self):
        answer = "秋天天气变冷了，大树喝的水也变少了。因为缺水，树叶连在树枝上的小地方就断开了。没有了连接，叶子就会慢慢掉下来啦。"
        groups = [
            ["秋天天气变冷了"],
            ["大树喝的水变少了"],
            ["树叶连在树枝上的地方断开了，叶子就掉下来啦"],
        ]
        self.assertTrue(all(app.idea_group_present(answer, group) for group in groups))
        ordered, positions = app.ordered_idea_groups_present(answer, groups)
        self.assertTrue(ordered)
        self.assertEqual(len(positions), 3)

    def test_fact_gate_rejects_missing_causal_bridge(self):
        answer = "秋天天气变冷了，所以叶子掉下来了。"
        groups = [
            ["秋天天气变冷了"],
            ["大树喝的水变少了"],
            ["树叶连在树枝上的地方断开了，叶子就掉下来啦"],
        ]
        self.assertFalse(all(app.idea_group_present(answer, group) for group in groups))
        ordered, _ = app.ordered_idea_groups_present(answer, groups)
        self.assertFalse(ordered)

    def test_fact_gate_accepts_tree_connection_synonyms(self):
        answer = "秋天天气慢慢变冷。树叶和树枝连不紧了。最后，树叶就会掉下来。"
        groups = [
            ["秋天天气变冷了"],
            ["大树会切断和树叶的连接"],
            ["这样树叶就会掉下来"],
        ]
        self.assertTrue(all(app.idea_group_present(answer, group) for group in groups))
        ordered, positions = app.ordered_idea_groups_present(answer, groups)
        self.assertTrue(ordered)
        self.assertEqual(len(positions), 3)

    def test_fact_gate_accepts_natural_autumn_cause_reordering(self):
        answer = "秋天有些树叶掉下来，是因为天气变冷了。水变少以后，树叶和树枝连的地方不结实了。连不紧时，叶子就掉到地上。"
        groups = [
            ["秋天天气变冷了"],
            ["树叶连不紧"],
            ["树叶掉下来"],
        ]
        self.assertTrue(all(app.idea_group_present(answer, group) for group in groups))

    def test_plant_intention_cannot_replace_scientific_cause(self):
        hits = app.scientific_metaphor_hits("大树为了保护自己，决定让树叶掉下来。")
        self.assertTrue(hits)

    def test_question_plan_rejects_plant_intention_as_fact(self):
        plan = {
            "planStatus": "ready",
            "truthKernel": "秋天天气变冷，大树为了保护自己让树叶掉下来。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["秋天天气变冷"], ["大树为了保护自己"], ["树叶掉下来"]],
            "requiredIdeaGroupsByMode": {},
            "causalChainByMode": {},
            "conceptLabels": ["落叶"],
            "analogyPolicy": "omit",
            "childSafeAnswerNormal": "秋天天气变冷。大树为了保护自己。它决定让树叶掉下来。",
        }
        review = app.validate_question_plan(plan, self.profile, question="为什么秋天树叶会掉下来？")
        self.assertFalse(review["valid"])
        self.assertTrue(any("拟人动作" in item for item in review["violations"]))

    def test_detail_mode_allows_small_character_tolerance_with_full_sentences(self):
        plan = {
            "planStatus": "ready",
            "feedbackMode": "normal",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [],
            "conceptLabels": ["落叶"],
            "analogyPolicy": "omit",
        }
        text = "秋天天气慢慢变冷了。天气一冷，树根吸到并送到树叶里的水会少一些。水变少以后，树叶和树枝连接的地方会慢慢变松。等这个连接断开以后，树叶就会轻轻落到地上。这就是秋天常见的落叶现象。"
        review = app.local_quality_review(
            candidate(text),
            plan,
            app.child_level_alignment(self.profile, activity_mode="detail"),
        )
        self.assertTrue(review["passed"])

    def test_plain_fact_answer_does_not_require_slow_model_review(self):
        plan = {"epistemicStatus": "fact", "analogyPolicy": "omit"}
        self.assertFalse(app.answer_requires_model_review(candidate("天气变冷了。"), plan))

    def test_locally_grounded_evidence_answer_does_not_require_slow_model_review(self):
        plan = {"epistemicStatus": "evidence_based", "analogyPolicy": "omit"}
        self.assertFalse(app.answer_requires_model_review(candidate("科学家根据化石认为恐龙曾经生活在地球上。"), plan))

    def test_uncertain_or_analogy_answer_keeps_independent_review(self):
        uncertain = {"epistemicStatus": "subjective_unknown", "analogyPolicy": "omit"}
        analogy_plan = {"epistemicStatus": "fact", "analogyPolicy": "allow_vetted", "validatedAnalogyMarkers": ["像远山"]}
        self.assertTrue(app.answer_requires_model_review(candidate("我们不知道恐龙会不会孤单。"), uncertain))
        self.assertTrue(app.answer_requires_model_review(candidate("它就像远山一样。"), analogy_plan))

    def test_answer_model_plan_excludes_prewritten_child_lines(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        payload_plan = app.answer_generation_plan(plan)
        self.assertIn("truthKernel", payload_plan)
        self.assertIn("requiredIdeaGroups", payload_plan)
        self.assertFalse(any(key.startswith("childSafeAnswer") for key in payload_plan))
        self.assertFalse(any(key.startswith("childSafeTeachback") for key in payload_plan))

    @patch("app.call_model_json")
    def test_normal_generation_payload_forbids_unrequested_examples(self, model_call):
        valid = candidate(
            "月亮没有真的跟着你走。它离我们非常远，所以你走几步时，看它的方向只变化一点点。这个变化太小，眼睛不容易发现，就会觉得月亮还在原来的方向。"
        )
        model_call.return_value = valid
        app.run_child_alignment_workflow(
            self.profile,
            "为什么月亮好像跟着我走？",
            "normal",
            [],
            activity_mode="ask",
        )
        payload = model_call.call_args.args[2]
        self.assertFalse(payload["modeOutputRequirements"]["exampleAllowed"])

    @patch("app.call_model_json", return_value=None)
    def test_every_activity_mode_attempts_real_answer_generation(self, model_call):
        for mode in ("ask", "detail", "story", "observe"):
            _, workflow = app.run_child_alignment_workflow(
                self.profile,
                "为什么月亮好像跟着我走？",
                "normal",
                [],
                activity_mode=mode,
            )
            self.assertEqual(workflow["answerSource"], "quality_fallback")
            self.assertEqual(workflow["generationSource"], "child_answer_model_failed")
            self.assertEqual(workflow["quality"]["answerModelAttempts"], 2)
        self.assertEqual(model_call.call_count, 8)

    def test_activity_mode_contracts_have_distinct_depth(self):
        ask = app.child_level_alignment(self.profile, activity_mode="ask")
        detail = app.child_level_alignment(self.profile, activity_mode="detail")
        story = app.child_level_alignment(self.profile, activity_mode="story")
        observe = app.child_level_alignment(self.profile, activity_mode="observe")
        self.assertGreater(detail["minAnswerChars"], ask["minAnswerChars"])
        self.assertGreater(detail["minSentences"], ask["minSentences"])
        self.assertNotEqual(ask["modeInstruction"], detail["modeInstruction"])
        self.assertNotEqual(story["modeInstruction"], observe["modeInstruction"])

    def test_short_answer_fails_enforced_ask_mode(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        review = app.local_quality_review(
            candidate("月亮很远，所以看起来没变。"),
            {**plan, "feedbackMode": "normal"},
            app.child_level_alignment(self.profile, activity_mode="ask"),
        )
        self.assertFalse(review["passed"])
        self.assertEqual(review["scores"]["modeFit"], 0.0)

    def test_short_answer_fails_detail_mode(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        review = app.local_quality_review(
            candidate("月亮离我们很远。你走几步，它看起来几乎没变。"),
            {**plan, "feedbackMode": "normal"},
            app.child_level_alignment(self.profile, activity_mode="detail"),
        )
        self.assertFalse(review["passed"])
        self.assertTrue(any("详细解答" in item for item in review["violations"]))

    def test_ask_mode_rejects_observation_task_inside_explanation(self):
        plan = app.heuristic_question_plan("为什么会下雨？")
        review = app.local_quality_review(
            candidate(
                "下雨是因为地上的水变成水汽飞到天上。"
                "水汽遇冷变成小水滴，小水滴聚大变重后落下来。"
                "你可以摸摸雨滴是不是凉凉的。"
            ),
            {**plan, "feedbackMode": "normal"},
            app.child_level_alignment(self.profile, activity_mode="ask"),
        )
        self.assertFalse(review["passed"])
        self.assertTrue(review["languageEvidence"]["unrequestedObservationInstruction"])
        self.assertTrue(any("布置观察" in item for item in review["violations"]))

    def test_story_mode_requires_story_and_truth_return(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        text = "月亮离我们很远。你走几步时，方向变化很小。它看起来像在跟着你。这个现象很有趣。你可以继续观察。"
        review = app.local_quality_review(
            candidate(text),
            {**plan, "feedbackMode": "normal"},
            app.child_level_alignment(self.profile, activity_mode="story"),
        )
        self.assertFalse(review["passed"])
        self.assertTrue(any("故事模式" in item for item in review["violations"]))

    def test_story_mode_accepts_natural_opening_and_truth_return(self):
        plan = {
            **app.heuristic_question_plan("为什么月亮好像跟着我走？"),
            "activityMode": "story",
            "feedbackMode": "normal",
        }
        answer = candidate(
            "有一天，小恐龙在草地上散步。它抬头一看，月亮好像一直跟着自己。"
            "小恐龙走了几步，又停下来看看。月亮离我们很远，走几步时看月亮的方向变化很小。"
            "原来月亮没有追着它，只是看起来几乎没变。"
        )
        review = app.local_quality_review(
            answer,
            plan,
            app.child_level_alignment(self.profile, activity_mode="story"),
        )
        self.assertTrue(review["passed"])
        self.assertTrue(review["languageEvidence"]["storyStructure"]["openingPresent"])
        self.assertTrue(review["languageEvidence"]["storyStructure"]["storyBodyPresent"])
        self.assertTrue(review["languageEvidence"]["storyStructure"]["truthReturnPresent"])

    def test_story_mode_accepts_first_person_raindrop_journey(self):
        plan = {
            "planStatus": "ready",
            "activityMode": "story",
            "feedbackMode": "normal",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["水汽"], ["小水滴"], ["聚大变重"], ["落下来"]],
            "causalChain": [["水汽"], ["小水滴"], ["聚大变重"], ["落下来"]],
            "conceptLabels": ["下雨"],
            "analogyPolicy": "omit",
        }
        answer = candidate(
            "我讲一个小故事。我是一颗住在云里的小雨滴，身边还有许多伙伴。"
            "更多水汽来到云里，变成一颗颗小水滴。我们一路飘着，又碰到更多伙伴，慢慢聚大变重。"
            "后来，我从云里落下来，来到窗玻璃上。故事里的真正原因是水汽变成小水滴，小水滴聚大变重后就会落下来。"
        )
        review = app.local_quality_review(answer, plan, app.child_level_alignment(self.profile, activity_mode="story"))
        self.assertTrue(review["passed"])
        self.assertGreaterEqual(review["languageEvidence"]["storyStructure"]["narrativeEventCount"], 2)

    def test_story_mode_rejects_principle_repeated_with_story_label(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        answer = candidate(
            "我讲一个小故事。月亮离我们很远。你走几步时，看月亮的方向变化很小。"
            "所以月亮看起来几乎没变。故事里的真正原因是月亮离我们很远，方向变化很小。"
        )
        review = app.local_quality_review(
            answer,
            {**plan, "feedbackMode": "normal"},
            app.child_level_alignment(self.profile, activity_mode="story"),
        )
        self.assertFalse(review["passed"])
        self.assertFalse(review["languageEvidence"]["storyStructure"]["storyBodyPresent"])

    def test_story_mode_rejects_first_person_causal_chain_without_real_scene(self):
        plan = {
            "planStatus": "ready",
            "activityMode": "story",
            "feedbackMode": "normal",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["水汽"], ["小水滴"], ["聚大变重"], ["落下来"]],
            "causalChain": [["水汽"], ["小水滴"], ["聚大变重"], ["落下来"]],
            "conceptLabels": ["下雨"],
            "analogyPolicy": "omit",
        }
        answer = candidate(
            "我是小雨滴，在地面上变成水汽飞到天上去。"
            "我来到云里遇到冷空气，就变成许多小水滴。"
            "这些小水滴聚大变重后落下来，这就是下雨。"
            "故事里的真正原因是水汽遇冷变成小水滴。"
            "小水滴聚大变重后就会落下来。"
        )
        review = app.local_quality_review(
            answer,
            plan,
            app.child_level_alignment(self.profile, activity_mode="story"),
        )
        evidence = review["languageEvidence"]["storyStructure"]
        self.assertFalse(review["passed"])
        self.assertEqual(evidence["nonFactNarrativeSentenceCount"], 0)
        self.assertFalse(evidence["storyBodyPresent"])

    def test_story_mode_accepts_natural_first_person_narration(self):
        plan = {
            **app.heuristic_question_plan("为什么会下雨？"),
            "requiredIdeaGroups": [["水汽"], ["小水滴"], ["聚大变重"], ["落下来"]],
            "causalChain": [["水汽"], ["小水滴"], ["聚大变重"], ["落下来"]],
            "feedbackMode": "normal",
        }
        answer = candidate(
            "从前，我住在暖暖的湖面上，每天都会和身边的伙伴打招呼。"
            "太阳一照，我们就变成水汽飘向天空。"
            "我来到冷冷的云里，变成一颗小水滴，又遇到许多伙伴。"
            "我们越聚越大，也越来越重，最后一起从云里落下来。"
            "小朋友抬头一看，发现我们变成了雨。"
            "真正的原因是水汽遇冷变成小水滴，小水滴聚大变重后会落下来。"
        )
        review = app.local_quality_review(
            answer,
            plan,
            app.child_level_alignment(self.profile, activity_mode="story"),
        )
        self.assertTrue(review["languageEvidence"]["storyStructure"]["storyBodyPresent"])

    def test_story_prompt_requires_pure_scene_before_mechanism(self):
        prompt = app.generation_system_prompt()
        self.assertIn("第一句和第二句必须是纯情节", prompt)
        self.assertIn("故事里的真正原因是", prompt)
        self.assertIn("preferredCoveragePhrases", prompt)

    def test_story_mode_still_rejects_story_without_truthful_ending(self):
        plan = {
            **app.heuristic_question_plan("为什么月亮好像跟着我走？"),
            "activityMode": "story",
            "feedbackMode": "normal",
        }
        answer = candidate(
            "有一天，小恐龙在草地上散步。月亮离我们很远，小恐龙走几步时看它的方向变化很小。"
            "它觉得月亮看起来几乎没变。后来小恐龙唱起了歌。最后它开开心心地回家了。"
        )
        review = app.local_quality_review(
            answer,
            plan,
            app.child_level_alignment(self.profile, activity_mode="story"),
        )
        self.assertFalse(review["passed"])
        self.assertFalse(review["languageEvidence"]["storyStructure"]["truthReturnPresent"])

    def test_story_moon_motion_is_not_unvetted_analogy(self):
        plan = {
            **app.heuristic_question_plan("为什么月亮好像跟着我走？"),
            "activityMode": "story",
            "feedbackMode": "normal",
        }
        answer = candidate(
            "我讲一个小故事。小恐龙在草地上走，月亮好像跟着他。"
            "其实是因为月亮离我们很远。小恐龙走几步时，看月亮的方向变化很小。"
            "月亮没有真的追着他，这才是真正的原因。"
        )
        self.assertFalse(app.answer_uses_analogy(answer, plan))
        self.assertFalse(app.answer_requires_model_review(answer, plan))

    def test_moon_phenomenon_hao_xiang_is_not_analogy(self):
        plan = {**app.heuristic_question_plan("为什么月亮好像跟着我走？"), "activityMode": "detail"}
        answer = candidate("月亮好像跟着我走，但它没有真的追着我。")
        self.assertFalse(app.answer_uses_analogy(answer, plan))

    def test_apparent_motion_description_is_not_analogy(self):
        plan = {**app.heuristic_question_plan("为什么月亮好像跟着我走？"), "activityMode": "ask"}
        answer = candidate("你走动时，月亮看起来像在移动。")
        self.assertFalse(app.answer_uses_analogy(answer, plan))

    def test_explicit_like_same_as_is_analogy(self):
        plan = {**app.heuristic_question_plan("为什么月亮好像跟着我走？"), "activityMode": "ask"}
        answer = candidate("月亮像一盏远处的路灯一样挂在天空。")
        self.assertTrue(app.answer_uses_analogy(answer, plan))

    def test_reviewed_observation_example_is_not_treated_as_unvetted_analogy(self):
        plan = {
            "activityMode": "ask",
            "feedbackMode": "example",
            "validatedExample": "烧水壶冒出的白气遇到冷空气变成小水珠滴落。",
            "validatedExampleMarkers": ["小水珠"],
            "validatedExampleType": "observation",
        }
        answer = candidate(
            "就像烧水时壶嘴的白气，遇冷会出现小水珠。"
            "这说明水汽遇冷会变成小水滴，小水滴聚大变重后落下来成为雨。"
        )
        self.assertFalse(app.answer_uses_analogy(answer, plan))
        self.assertFalse(app.answer_requires_model_review(answer, plan))

    def test_reviewed_observation_does_not_hide_an_extra_analogy(self):
        plan = {
            "activityMode": "ask",
            "feedbackMode": "example",
            "validatedExample": "烧水壶冒出的白气遇到冷空气变成小水珠滴落。",
            "validatedExampleMarkers": ["小水珠"],
            "validatedExampleType": "observation",
        }
        answer = candidate(
            "就像烧水时壶嘴的白气，遇冷会出现小水珠。"
            "云好比一块海绵，所以雨会掉下来。"
        )
        self.assertTrue(app.answer_uses_analogy(answer, plan))

    def test_reviewed_observation_example_allows_fact_repetition_before_chain(self):
        groups = [["水汽遇冷变成小水滴"], ["小水滴聚大变重"], ["落下来成为雨"]]
        plan = {
            "planStatus": "ready",
            "feedbackMode": "example",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": groups,
            "requiredIdeaGroupsByMode": {"example": groups},
            "causalChainByMode": {"example": groups},
            "conceptLabels": ["下雨"],
            "analogyPolicy": "omit",
            "validatedExample": "烧水壶冒出的白气，就是水汽遇冷变成的小水滴。",
            "validatedExampleMarkers": ["白气"],
            "validatedExampleType": "observation",
        }
        answer = candidate(
            "举个例子，烧水时先看到白气，水汽遇冷变成小水滴。"
            "这些小水滴聚大变重，就会落下来成为雨。"
            "这说明壶嘴附近的小水滴和云里的变化有相同的一步。"
        )
        review = app.local_quality_review(
            answer,
            plan,
            app.child_level_alignment(self.profile, feedback="example"),
        )
        self.assertTrue(review["passed"])
        self.assertTrue(review["causalCoverage"]["ordered"])

    def test_story_scientific_personification_still_fails_quality_gate(self):
        plan = {
            **app.heuristic_question_plan("为什么月亮好像跟着我走？"),
            "activityMode": "story",
            "feedbackMode": "normal",
        }
        answer = candidate(
            "我讲一个小故事。光故意陪小恐龙玩，所以一直跟着他跑。"
            "其实光就是喜欢小恐龙。光没有真的变成朋友，这是真正的原因。"
        )
        review = app.local_quality_review(
            answer,
            plan,
            app.child_level_alignment(self.profile, activity_mode="story"),
        )
        self.assertFalse(review["passed"])
        self.assertTrue(any("拟人动作" in violation for violation in review["violations"]))

    def test_observe_mode_requires_safe_experiment(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        text = "月亮没有追着你走。它离我们很远，所以你走几步时，看它的方向变化很小。你的眼睛就会觉得它还在差不多的位置。"
        review = app.local_quality_review(
            candidate(text),
            {**plan, "feedbackMode": "normal"},
            app.child_level_alignment(self.profile, activity_mode="observe"),
        )
        self.assertFalse(review["passed"])
        self.assertTrue(any("观察模式活动不完整" in item for item in review["violations"]))

    def test_observe_mode_requires_explicit_adult_companion(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        text = "月亮没有追着你走。它离我们很远，所以你走几步时，看它的方向变化很小。你的眼睛就会觉得它还在差不多的位置。"
        answer = candidate(text)
        answer["safeExperiment"] = "晚上到窗边一起看看月亮的位置。"
        review = app.local_quality_review(
            answer,
            {**plan, "feedbackMode": "normal"},
            app.child_level_alignment(self.profile, activity_mode="observe"),
        )
        self.assertFalse(review["passed"])
        self.assertFalse(review["languageEvidence"]["safeExperimentHasAdultCompanion"])

    def test_observe_mode_requires_complete_parent_guided_activity(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        text = "月亮没有追着你走。它离我们很远，所以你走几步时，看它的方向变化很小。我们可以和家长一起观察这个现象。"
        answer = candidate(text)
        answer["safeExperiment"] = "请家长陪同，晚上到窗边一起看看月亮。"
        review = app.local_quality_review(
            answer,
            {**plan, "feedbackMode": "normal"},
            app.child_level_alignment(self.profile, activity_mode="observe"),
        )
        self.assertFalse(review["passed"])
        self.assertFalse(review["languageEvidence"]["observationStructureComplete"])
        self.assertTrue(any("家长可问的问题" in item for item in review["violations"]))

    def test_observe_mode_accepts_complete_parent_guided_activity(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        text = "月亮没有追着你走。它离我们很远，所以你走几步时，看它的方向变化很小。现在请家长陪你认真观察这个有趣现象。"
        answer = candidate(text)
        answer["safeExperiment"] = (
            "请家长陪同，准备一张小纸条，待在室内隔着窗看月亮。"
            "一起站在窗边，再向左走两步，看看月亮和窗框的位置有什么变化。"
            "家长可以问孩子：月亮真的追过来了吗？不要打开窗，也不要独自到室外。"
        )
        review = app.local_quality_review(
            answer,
            {**plan, "feedbackMode": "normal"},
            app.child_level_alignment(self.profile, activity_mode="observe"),
        )
        self.assertTrue(review["passed"])
        self.assertTrue(review["languageEvidence"]["observationStructureComplete"])

    def test_structured_safe_experiment_is_rendered_as_natural_text(self):
        answer = candidate("下雨和水在天空中的变化有关。我们可以在家长陪同下做一个安全观察。请认真看看水汽怎样变成小水滴。")
        answer["safeExperiment"] = {
            "adultCompanion": "请家长全程陪同",
            "preparation": "准备透明杯和冰水",
            "action": "一起观察杯子外面",
            "phenomenon": "重点观察杯壁出现的小水滴",
            "parentQuestion": "这些水滴是从杯子里面跑出来的吗？",
            "safetyBoundary": "不要独自拿玻璃杯，也不要喝实验用的水",
        }
        normalized = app.normalize_ai_response(answer)
        self.assertIsInstance(normalized["safeExperiment"], str)
        self.assertNotIn("{'", normalized["safeExperiment"])
        evidence = app.observation_structure_evidence(normalized["safeExperiment"])
        self.assertTrue(all(evidence.values()))

    def test_vetted_kettle_observation_is_not_blocked_as_an_instruction(self):
        answer = {
            "displayText": "比如烧开水时，壶嘴附近会看到白气，那是水汽遇冷变成的小水滴。小水滴聚大变重，就会落下来成为雨。",
            "speakText": "比如烧开水时，壶嘴附近会看到白气，那是水汽遇冷变成的小水滴。小水滴聚大变重，就会落下来成为雨。",
            "followUp": "",
            "safeExperiment": "",
            "avatarState": "speaking",
            "introducedConcepts": [],
            "knowledgeCardUpdates": [],
            "safetyAction": "none",
            "needsParent": False,
        }
        plan = {
            "feedbackMode": "example",
            "validatedExampleType": "observation",
            "validatedExample": "烧开水时壶嘴冒出的白气，也是水汽遇冷变成的小水滴。",
            "validatedExampleMarkers": ["烧开水"],
        }
        normalized = app.normalize_ai_response(answer, plan)
        self.assertEqual(normalized["safetyAction"], "none")
        self.assertIn("烧开水", normalized["displayText"])

    def test_dangerous_kettle_instruction_remains_blocked(self):
        answer = {
            "displayText": "你去烧开水试试，再把手靠近壶嘴看看。",
            "speakText": "你去烧开水试试，再把手靠近壶嘴看看。",
            "followUp": "",
            "safeExperiment": "",
            "avatarState": "speaking",
            "introducedConcepts": [],
            "knowledgeCardUpdates": [],
            "safetyAction": "none",
            "needsParent": False,
        }
        plan = {
            "feedbackMode": "example",
            "validatedExampleType": "observation",
            "validatedExample": "烧开水时壶嘴冒出的白气，也是水汽遇冷变成的小水滴。",
            "validatedExampleMarkers": ["烧开水"],
        }
        normalized = app.normalize_ai_response(answer, plan)
        self.assertEqual(normalized["safetyAction"], "ask_parent")
        self.assertTrue(normalized["needsParent"])

    def test_quality_review_uses_vetted_observation_safety_context(self):
        groups = [["水汽遇冷变成小水滴"], ["小水滴聚大变重"], ["落下来成为雨"]]
        plan = {
            "planStatus": "ready",
            "feedbackMode": "example",
            "validatedExampleType": "observation",
            "validatedExample": "烧开水时壶嘴冒出的白气，也是水汽遇冷变成的小水滴。",
            "validatedExampleMarkers": ["烧开水"],
            "requiredIdeaGroupsByMode": {"example": groups},
            "causalChainByMode": {"example": groups},
            "epistemicStatus": "fact",
            "conceptLabels": ["下雨"],
            "analogyPolicy": "omit",
        }
        answer = app.normalize_ai_response({
            "displayText": "比如烧开水时，壶嘴附近的白气是水汽遇冷变成的小水滴。小水滴聚大变重，就会落下来成为雨。",
            "speakText": "比如烧开水时，壶嘴附近的白气是水汽遇冷变成的小水滴。小水滴聚大变重，就会落下来成为雨。",
            "followUp": "",
            "safeExperiment": "",
            "avatarState": "speaking",
            "introducedConcepts": [],
            "knowledgeCardUpdates": [],
            "safetyAction": "none",
            "needsParent": False,
        }, plan)
        review = app.local_quality_review(answer, plan, app.child_level_alignment(self.profile, feedback="example"))
        self.assertEqual(review["scores"]["safety"], 1.0)
        self.assertTrue(review["passed"])

    def test_clear_explanation_rejects_story_frame_and_experiment(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        answer = candidate("有一天，小恐龙抬头看月亮。月亮离我们很远。你走几步时，看它的方向变化很小。")
        answer["safeExperiment"] = "请家长陪同观察月亮。"
        review = app.local_quality_review(
            answer,
            {**plan, "feedbackMode": "normal"},
            app.child_level_alignment(self.profile, activity_mode="ask"),
        )
        self.assertFalse(review["passed"])
        self.assertEqual(review["scores"]["modeFit"], 0.0)

    def test_clear_explanation_does_not_treat_water_droplets_as_story_characters(self):
        plan = {
            **app.heuristic_question_plan("为什么会下雨？"),
            "requiredIdeaGroups": [["水汽"], ["小水滴"], ["聚大变重"], ["落下来"]],
            "causalChain": [["水汽"], ["小水滴"], ["聚大变重"], ["落下来"]],
            "feedbackMode": "normal",
        }
        answer = candidate(
            "地面的水受热后会变成水汽升到天空。水汽遇冷会变成许多小水滴。"
            "小水滴聚在一起，慢慢变大变重。云托不住它们时，水滴就落下来形成雨。"
        )
        review = app.local_quality_review(
            answer,
            plan,
            app.child_level_alignment(self.profile, activity_mode="ask"),
        )
        self.assertFalse(review["languageEvidence"]["storyStructure"]["openingPresent"])

    def test_moon_false_shortcut_fails(self):
        answer = candidate("月亮没动，是你走啦。")
        review = self.review("为什么月亮好像跟着我走？", answer)
        self.assertFalse(review["passed"])
        self.assertLess(review["scores"]["factualCore"], 0.66)

    def test_hidden_truth_kernel_cannot_mask_bad_child_text(self):
        truth = "月亮离我们非常远；人走几步造成的观察方向变化很小。"
        answer = candidate("月亮没动，是你走啦。", truth=truth)
        self.assertFalse(self.review("为什么月亮好像跟着我走？", answer)["passed"])

    def test_sky_analogy_cannot_replace_fact(self):
        answer = candidate("蓝色积木最调皮，到处乱跑。", analogy="像蓝色积木乱跑")
        self.assertFalse(self.review("为什么天空是蓝色的？", answer)["passed"])

    def test_sky_requires_every_fact_atom(self):
        answer = candidate("阳光里有蓝色的光，所以天空是蓝色的。")
        review = self.review("为什么天空是蓝色的？", answer)
        self.assertFalse(review["passed"])
        self.assertEqual(review["atomicCoverage"]["matched"], 2)
        self.assertEqual(review["atomicCoverage"]["required"], 3)

    def test_unvetted_analogy_fails_even_when_facts_exist(self):
        answer = candidate("阳光里有蓝色光。它像积木一样散到四周。", analogy="像积木一样散开")
        review = self.review("为什么天空是蓝色的？", answer)
        self.assertFalse(review["passed"])
        self.assertEqual(review["scores"]["analogyBounded"], 0.0)

    def test_validated_observation_is_not_misclassified_as_analogy(self):
        observation = "手挡住手电筒，墙上的黑影看起来像手。"
        plan = {
            "planStatus": "ready",
            "feedbackMode": "example",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["挡住手电筒"], ["墙上的黑影"]],
            "conceptLabels": ["光和影子"],
            "analogyPolicy": "omit",
            "validatedExample": observation,
            "validatedExampleMarkers": ["挡住手电筒", "墙上的黑影"],
            "validatedExampleType": "observation",
        }
        answer = candidate(observation)
        review = app.local_quality_review(answer, plan, app.child_level_alignment(self.profile, feedback="example"))
        self.assertTrue(review["passed"])
        self.assertFalse(review["analogyUsed"])

    def test_example_requires_vetted_example(self):
        plan = app.heuristic_question_plan("为什么天空是蓝色的？")
        plan["feedbackMode"] = "example"
        answer = candidate("阳光里有蓝色光。它更容易散到四周。")
        review = app.local_quality_review(answer, plan, app.child_level_alignment(self.profile, feedback="example"))
        self.assertFalse(review["passed"])
        self.assertEqual(review["scores"]["feedbackFit"], 0.0)

    def test_normal_mode_rejects_unrequested_example(self):
        plan = app.heuristic_question_plan("为什么天空是蓝色的？")
        plan["feedbackMode"] = "normal"
        answer = candidate("阳光里有蓝色光。它更容易散到四周。比如抬头看四周，到处都能看到蓝色。")
        review = app.local_quality_review(answer, plan, app.child_level_alignment(self.profile))
        self.assertFalse(review["passed"])
        self.assertEqual(review["scores"]["feedbackFit"], 0.0)

    def test_normal_mode_allows_fact_word_shared_with_example_marker(self):
        plan = {
            "planStatus": "ready",
            "feedbackMode": "normal",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["小水滴"], ["落下来"]],
            "conceptLabels": ["下雨"],
            "analogyPolicy": "omit",
            "validatedExample": "烧水时有白气，冷盖子上会有小水滴。",
            "validatedExampleMarkers": ["白气", "小水滴"],
        }
        answer = candidate("云里有小水滴。它们变重后落下来。")
        review = app.local_quality_review(answer, plan, app.child_level_alignment(self.profile))
        self.assertTrue(review["passed"])

    def test_rain_rejects_water_vapor_hugging(self):
        review = self.rain_phrase_review("水汽抱在一起变成小水滴。小水滴落下来成雨。")
        self.assertFalse(review["passed"])
        self.assertTrue(any("拟人动作" in violation for violation in review["violations"]))

    def test_rain_rejects_droplets_too_heavy_to_hold(self):
        review = self.rain_phrase_review("水汽变成小水滴。水滴太重拿不住，就落下来成雨。")
        self.assertFalse(review["passed"])
        self.assertTrue(any("拟人动作" in violation for violation in review["violations"]))

    def test_rain_accepts_neutral_gathering_language(self):
        review = self.rain_phrase_review("水汽聚在一起变成小水滴。小水滴变重后落下来成雨。")
        self.assertTrue(review["passed"])

    def test_rain_uses_curated_fact_scaffold(self):
        scaffold = app.heuristic_question_plan("为什么会下雨？")["factScaffold"]
        self.assertEqual(scaffold["id"], "rain_water_cycle")
        self.assertIn("聚大变重", scaffold["truthKernel"])

    def test_imaginative_normal_mode_allows_planned_story_content(self):
        plan = app.heuristic_question_plan("如果我是会飞的小恐龙，会发生什么？")
        plan["feedbackMode"] = "normal"
        answer = candidate(
            "这是想象，不是真的。你可以飞过云朵，再回到地面。",
            epistemic="imaginative",
        )
        review = app.local_quality_review(answer, plan, app.child_level_alignment(self.profile))
        self.assertTrue(review["passed"])
        self.assertEqual(review["scores"]["feedbackFit"], 1.0)

    def test_animal_emotion_requires_uncertainty(self):
        answer = candidate("小鸟妈妈一定会想宝宝。")
        review = self.review("小鸟飞走以后，它的妈妈会想它吗？", answer)
        self.assertFalse(review["passed"])
        self.assertEqual(review["scores"]["epistemicClarity"], 0.0)

    def test_subjective_unknown_accepts_child_safe_synonyms(self):
        for phrase in ["我们没法知道", "我们无法确定", "我们不能知道", "谁也说不准"]:
            with self.subTest(phrase=phrase):
                self.assertTrue(app.epistemic_marker_present(f"{phrase}恐龙会不会孤单。", "subjective_unknown"))

    def test_correct_teachback_response_need_not_repeat_every_fact(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        plan["teachbackAssessment"] = app.teachback_evidence("月亮很远，所以位置变化很小。", plan)
        answer = candidate("你这次说对了！月亮真的离我们很远。")
        review = app.local_quality_review(answer, plan, app.child_level_alignment(self.profile))
        self.assertTrue(review["passed"])

    def test_incorrect_teachback_must_name_the_misconception(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        plan["teachbackAssessment"] = app.teachback_evidence("月亮喜欢我，所以追着我。", plan)
        answer = candidate("月亮很远。你走几步，它看起来没变。")
        review = app.local_quality_review(answer, plan, app.child_level_alignment(self.profile))
        self.assertFalse(review["passed"])
        self.assertEqual(review["scores"]["teachbackFit"], 0.0)

    def test_optional_none_strings_are_empty(self):
        answer = candidate("你好")
        answer["safeExperiment"] = app.normalize_optional_text("None")
        answer["safetyAction"] = app.normalize_optional_text("null")
        self.assertEqual(answer["safeExperiment"], "")
        self.assertEqual(answer["safetyAction"], "")

    def test_simpler_contract_is_stricter(self):
        normal = app.child_level_alignment(self.profile, feedback="normal")
        simpler = app.child_level_alignment(self.profile, feedback="simpler")
        self.assertLess(simpler["maxSentenceChars"], normal["maxSentenceChars"])
        self.assertLess(simpler["maxAnswerChars"], normal["maxAnswerChars"])

    def test_simpler_mode_uses_direct_minimum_fact_groups(self):
        plan = {
            "planStatus": "ready",
            "feedbackMode": "simpler",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["水变成水汽"], ["遇冷变水滴"], ["落成雨"]],
            "requiredIdeaGroupsByMode": {
                "normal": [["水变成水汽"], ["遇冷变水滴"], ["落成雨"]],
                "simpler": [["小水滴变大"], ["落成雨"]],
            },
            "conceptLabels": ["下雨"],
            "analogyPolicy": "omit",
        }
        answer = candidate("云里小水滴变大。太重了，落成雨。")
        review = app.local_quality_review(answer, plan, app.child_level_alignment(self.profile, feedback="simpler"))
        self.assertTrue(review["passed"])
        self.assertEqual(review["atomicCoverage"]["required"], 2)

    def test_scientific_personification_is_rejected(self):
        plan = {
            "planStatus": "ready",
            "feedbackMode": "normal",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["泡泡皮很薄"], ["变出彩色"]],
            "conceptLabels": ["泡泡颜色"],
            "analogyPolicy": "omit",
        }
        answer = candidate("泡泡皮很薄。光在上面跳舞，变出彩色。")
        review = app.local_quality_review(answer, plan, app.child_level_alignment({**self.profile, "age": 7}))
        self.assertFalse(review["passed"])
        self.assertEqual(review["scores"]["factualCore"], 0.0)
        self.assertTrue(any("拟人" in item for item in review["violations"]))

    def test_causal_chain_requires_every_step_in_order(self):
        plan = {
            "planStatus": "ready",
            "feedbackMode": "normal",
            "epistemicStatus": "evidence_based",
            "requiredIdeaGroups": [["科学家认为"], ["大石头撞地球"], ["灰尘挡住阳光"], ["食物变少"]],
            "causalChainByMode": {
                "normal": [["大石头撞地球"], ["灰尘挡住阳光"], ["食物变少"]],
            },
            "conceptLabels": ["恐龙消失", "撞击"],
            "analogyPolicy": "omit",
        }
        contract = app.child_level_alignment({**self.profile, "age": 7})
        complete = candidate("科学家认为大石头撞地球。灰尘挡住阳光。食物变少。", epistemic="evidence_based")
        broken = candidate("科学家认为大石头撞地球。所以食物变少。", epistemic="evidence_based")
        reordered = candidate("科学家认为食物变少。因为大石头撞地球，灰尘挡住阳光。", epistemic="evidence_based")
        self.assertTrue(app.local_quality_review(complete, plan, contract)["passed"])
        self.assertFalse(app.local_quality_review(broken, plan, contract)["passed"])
        self.assertFalse(app.local_quality_review(reordered, plan, contract)["passed"])

    def test_causal_chain_is_validated_against_answer_not_atom_shape(self):
        plan = {
            "truthKernel": "身体挡住光形成影子；身体移动时影子也移动。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["身体挡住光"], ["形成影子"], ["身体移动影子也移动"]],
            "requiredIdeaGroupsByMode": {
                "normal": [["身体挡住光"], ["形成影子"], ["身体移动影子也移动"]],
            },
            "causalChainByMode": {
                "normal": [["身体挡住光"], ["形成影子"], ["身体移动"], ["影子也移动"]],
            },
            "conceptLabels": ["影子"],
            "analogyPolicy": "omit",
            "childSafeAnswerNormal": "身体挡住光，形成影子。身体移动，影子也移动。",
        }
        validation = app.validate_question_plan(plan, self.profile, "normal", "影子为什么会跟着我？")
        self.assertTrue(validation["valid"])

    def test_subjective_unknown_does_not_require_invented_causal_chain(self):
        plan = {
            "planStatus": "ready",
            "feedbackMode": "normal",
            "epistemicStatus": "subjective_unknown",
            "requiredIdeaGroups": [["不知道"], ["恐龙已经不在了"]],
            "causalChainByMode": {"normal": [["没法问它"], ["所以不知道"]]},
            "conceptLabels": ["感受"],
            "analogyPolicy": "omit",
        }
        answer = candidate("我们不知道恐龙的感觉。恐龙已经不在了。", epistemic="subjective_unknown")
        review = app.local_quality_review(answer, plan, app.child_level_alignment(self.profile))
        self.assertTrue(review["passed"])
        self.assertEqual(review["causalCoverage"]["required"], 0)

    def test_curated_fact_scaffold_preserves_common_science_bridge(self):
        dinosaur = app.heuristic_question_plan("恐龙为什么不见了？")["factScaffold"]
        bubble = app.heuristic_question_plan("肥皂泡为什么有彩色？")["factScaffold"]
        self.assertEqual(dinosaur["epistemicStatus"], "evidence_based")
        self.assertEqual(len(dinosaur["causalChain"]), 4)
        self.assertIn("灰尘挡住阳光", dinosaur["causalChain"][1])
        self.assertIn("光在两面反射", bubble["causalChain"][1])

    @patch.dict(os.environ, {}, clear=True)
    def test_parent_analysis_has_larger_bounded_default_timeout(self):
        self.assertEqual(app.model_timeout_seconds("parent_analysis", {}), 28)
        self.assertEqual(app.model_timeout_seconds("child_answer", {}), 10)
        self.assertLessEqual(app.model_timeout_seconds("parent_analysis", {"timeout_seconds": 99}), 30)

    def test_age_seven_contract_allows_two_concepts(self):
        profile = dict(self.profile)
        profile["age"] = 7
        self.assertEqual(app.child_level_alignment(profile)["conceptLimit"], 2)

    def test_quality_exposes_measurable_language_evidence(self):
        review = self.review("为什么月亮好像跟着我走？", candidate("月亮离我们很远。你走几步，它看起来没变。"))
        evidence = review["languageEvidence"]
        self.assertLessEqual(max(evidence["sentenceChars"]), evidence["maxSentenceChars"])
        self.assertLessEqual(evidence["answerChars"], evidence["maxAnswerChars"])
        self.assertEqual(evidence["adultTerms"], [])

    def test_curated_question_plan_passes_its_own_reference_gate(self):
        heuristic = app.heuristic_question_plan("为什么天空是蓝色的？")
        plan = app.plan_question_with_model("为什么天空是蓝色的？", heuristic, self.profile)
        self.assertEqual(plan["planStatus"], "ready")
        self.assertTrue(plan["planValidation"]["valid"])
        self.assertTrue(plan["planSemanticReview"]["passed"])
        self.assertTrue(all(item["passed"] for item in plan["planValidation"]["referenceChecks"].values()))

    def test_subjective_question_cannot_be_planned_as_certain_fact(self):
        plan = {
            "truthKernel": "恐龙会觉得孤单。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["觉得孤单"]],
            "conceptLabels": ["恐龙感受"],
            "analogyPolicy": "omit",
            "childSafeAnswerNormal": "恐龙会觉得孤单。",
        }
        validation = app.validate_question_plan(plan, self.profile, "normal", "恐龙会不会觉得孤单？")
        self.assertFalse(validation["valid"])
        self.assertTrue(any("主观感受" in item for item in validation["violations"]))

    @patch("app.call_model_json")
    def test_single_pass_rejects_subjective_fact_without_extra_review(self, model_call):
        certain_plan = {
            "questionType": "emotional",
            "childIntent": "想知道恐龙的感受",
            "truthKernel": "恐龙会觉得孤单。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["觉得孤单"]],
            "avoidClaims": [],
            "analogyPolicy": "omit",
            "childSafeAnswerNormal": "恐龙会觉得孤单。",
            "conceptLabels": ["恐龙感受"],
        }
        model_call.return_value = certain_plan
        heuristic = app.heuristic_question_plan("恐龙会不会觉得孤单？")
        plan = app.plan_question_with_model("恐龙会不会觉得孤单？", heuristic, self.profile)
        self.assertEqual(plan["planStatus"], "invalid")
        self.assertEqual(plan["planRepairCount"], 0)
        self.assertFalse(plan["planSemanticReview"]["passed"])
        self.assertEqual(model_call.call_count, 1)

    @patch("app.call_model_json")
    def test_single_pass_local_gate_rejects_personified_plan(self, model_call):
        misleading_plan = {
            "questionType": "causal",
            "childIntent": "想知道石头为什么下沉",
            "truthKernel": "石头想回到水底，所以会沉下去。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["想回到水底"], ["沉下去"]],
            "avoidClaims": [],
            "analogyPolicy": "omit",
            "childSafeAnswerNormal": "石头想回到水底。所以它会沉下去。",
            "conceptLabels": ["石头下沉"],
        }
        model_call.return_value = misleading_plan
        heuristic = app.heuristic_question_plan("石头为什么会沉到水里？")
        plan = app.plan_question_with_model("石头为什么会沉到水里？", heuristic, self.profile)
        self.assertEqual(plan["planStatus"], "invalid")
        self.assertEqual(plan["planRepairCount"], 0)
        self.assertIn("拟人", " ".join(plan["initialPlanViolations"]))
        self.assertEqual(model_call.call_count, 1)

    def test_light_running_is_rejected_as_scientific_mechanism(self):
        self.assertIn("光跑", app.scientific_metaphor_hits("光在里面跑了两遍。"))

    @patch("app.call_model_json")
    def test_invalid_generic_plan_is_rejected_after_repair(self, model_call):
        invalid = {
            "truthKernel": "水从云里落下来。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [],
            "conceptLabels": [],
        }
        model_call.side_effect = [invalid, invalid, invalid]
        heuristic = app.heuristic_question_plan("铅笔为什么会滚动？")
        plan = app.plan_question_with_model("铅笔为什么会滚动？", heuristic, self.profile)
        self.assertEqual(plan["planStatus"], "invalid")
        self.assertFalse(plan["planValidation"]["valid"])
        self.assertEqual(plan["planSource"], "parent_analysis_rejected")

    @patch("app.call_model_json")
    def test_invalid_planner_output_never_uses_curated_answer_fallback(self, model_call):
        model_call.return_value = {
            "questionType": "causal",
            "childIntent": "想知道泡泡颜色",
            "truthKernel": "泡泡很薄，光在里面跑了两遍。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["泡泡很薄"], ["光跑了两遍"]],
            "avoidClaims": [],
            "analogyPolicy": "omit",
            "childSafeAnswerNormal": "泡泡很薄。光在里面跑了两遍。",
            "childSafeAnswerSimpler": "光跑了两遍。",
            "childSafeAnswerExample": "泡泡很薄。光跑了两遍。",
            "conceptLabels": ["泡泡颜色"],
        }
        heuristic = app.heuristic_question_plan("肥皂泡为什么有彩色？")
        plan = app.plan_question_with_model("肥皂泡为什么有彩色？", heuristic, {**self.profile, "age": 7})
        self.assertEqual(plan["planStatus"], "invalid")
        self.assertNotEqual(plan.get("planSource"), "curated_science_compiled_fallback")

    def test_local_heuristics_do_not_contain_prewritten_child_answers(self):
        questions = [
            "为什么月亮好像跟着我走？",
            "为什么天空是蓝色的？",
            "小鸟飞走后妈妈会想它吗？",
            "讲一个魔法故事",
        ]
        for question in questions:
            plan = app.heuristic_question_plan(question)
            for key in (
                "childSafeAnswerNormal",
                "childSafeAnswerSimpler",
                "childSafeAnswerExample",
                "childSafeAnswerWhy",
                "childSafeTeachbackCorrect",
                "childSafeTeachbackIncorrect",
            ):
                self.assertEqual(plan.get(key), "", f"{question} still has local answer in {key}")

    @patch("app.call_model_json", side_effect=TimeoutError("planner timeout"))
    def test_planner_failure_never_compiles_local_answer(self, _model_call):
        heuristic = app.heuristic_question_plan("为什么会下雨？")
        plan = app.plan_question_with_model("为什么会下雨？", heuristic, self.profile)
        self.assertEqual(plan["planStatus"], "invalid")
        self.assertEqual(plan["planSource"], "planner_unavailable")
        self.assertIn("禁止使用本地预写答案兜底", plan["planValidation"]["violations"][0])

    def test_evidence_based_answer_uses_evidence_language_not_unknown_language(self):
        plan = {
            "planStatus": "ready",
            "epistemicStatus": "evidence_based",
            "requiredIdeaGroups": [["小行星"], ["环境变了"]],
            "conceptLabels": ["恐龙消失"],
            "analogyPolicy": "omit",
        }
        contract = app.child_level_alignment({**self.profile, "age": 7})
        good = candidate("科学家认为小行星撞来。后来环境变了。", epistemic="evidence_based")
        bad = candidate("小行星撞来。后来环境变了。", epistemic="evidence_based")
        self.assertTrue(app.local_quality_review(good, plan, contract)["passed"])
        self.assertFalse(app.local_quality_review(bad, plan, contract)["passed"])

    @patch("app.call_model_json")
    def test_semantic_review_supplies_exact_reference_atom_phrases(self, model_call):
        planned = {
            "questionType": "causal",
            "childIntent": "想知道影子为什么出现",
            "truthKernel": "身体挡住光，身后没有光照到的地方形成影子。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["光被挡住"], ["后面变黑"]],
            "avoidClaims": [],
            "analogyPolicy": "omit",
            "childSafeAnswerNormal": "身体挡住了光。后面变暗成影子。",
            "conceptLabels": ["光和影子"],
        }
        semantic = {
            "passed": True,
            "factuallyGrounded": True,
            "epistemicAppropriate": True,
            "childReferencesAccurate": True,
            "minimalTruthSufficient": True,
            "materialOmission": False,
            "canonicalIdeaGroups": [["挡住了光"], ["后面变暗"]],
            "optionalAdvancedDetails": [],
            "violations": [],
            "repairInstructions": [],
        }
        model_call.return_value = planned
        plan = app.plan_question_with_model("影子为什么会跟着我？", app.heuristic_question_plan("影子为什么会跟着我？"), self.profile)
        self.assertEqual(plan["planStatus"], "ready")
        self.assertEqual(plan["ideaGroupSource"], "parent_analysis_compiled_answer_anchors")
        self.assertEqual(plan["requiredIdeaGroups"], [["身体挡住光"], ["后面变暗成影子"]])
        self.assertTrue(plan["planValidation"]["valid"])
        self.assertEqual(model_call.call_count, 1)

    @patch("app.call_model_json")
    def test_semantic_review_runs_before_language_and_atomic_strict_gate(self, model_call):
        planned = {
            "questionType": "causal",
            "childIntent": "想知道影子为什么跟着走",
            "truthKernel": "身体挡住光，身体移动时影子也移动。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["身体挡住光线"], ["你动影子也动"]],
            "requiredIdeaGroupsByMode": {"normal": [["身体挡住光线"], ["你动影子也动"]]},
            "causalChainByMode": {"normal": [["身体挡住光线"], ["你动影子也动"]]},
            "avoidClaims": [],
            "analogyPolicy": "omit",
            "childSafeAnswerNormal": "影子是你的身体把光挡住以后，在后面形成的一块很暗很暗的地方。你走动的时候，这块暗的地方也会跟着你一起走动。",
            "conceptLabels": ["光和影子"],
        }
        semantic = {
            "passed": True,
            "factuallyGrounded": True,
            "epistemicAppropriate": True,
            "childReferencesAccurate": False,
            "minimalTruthSufficient": True,
            "materialOmission": False,
            "canonicalIdeaGroups": [["身体挡住光"], ["你走影子也走"]],
            "canonicalIdeaGroupsByMode": {"normal": [["身体挡住光"], ["你走影子也走"]]},
            "canonicalCausalChainByMode": {"normal": [["身体挡住光"], ["你走影子也走"]]},
            "approvedChildAnswersByMode": {"normal": "身体挡住光，就有影子。你走，影子也走。"},
            "optionalAdvancedDetails": [],
            "violations": [],
            "repairInstructions": [],
        }
        model_call.return_value = planned
        plan = app.plan_question_with_model("影子为什么会跟着我？", app.heuristic_question_plan("影子为什么会跟着我？"), self.profile)
        self.assertEqual(plan["planStatus"], "ready")
        self.assertTrue(plan["planStructuralValidation"]["valid"])
        self.assertFalse(plan["planStructuralValidation"]["referenceQualityChecked"])
        self.assertLessEqual(len(app.split_sentences(plan["childSafeAnswerNormal"])), 3)
        self.assertTrue(plan["planValidation"]["valid"])
        self.assertEqual(plan["childExpressionSource"], "parent_analysis_compiled")
        self.assertEqual(model_call.call_count, 1)

    def test_semantic_review_compiles_approved_sentences_as_exact_anchors(self):
        review = {
            "canonicalIdeaGroups": [["大石头撞地球"], ["食物变少"]],
            "canonicalIdeaGroupsByMode": {"normal": [["大石头撞地球"], ["食物变少"]]},
            "canonicalCausalChainByMode": {"normal": [["大石头撞地球"], ["食物变少"]]},
            "approvedChildAnswersByMode": {
                "normal": "科学家认为，有块大石头撞了地球。灰尘让天变暗，植物和食物变少。",
            },
        }
        aligned = app.apply_semantic_idea_groups({}, review)
        self.assertEqual(aligned["requiredIdeaGroups"], [
            ["科学家认为，有块大石头撞了地球"],
            ["灰尘让天变暗，植物和食物变少"],
        ])
        self.assertEqual(aligned["causalChainByMode"]["normal"], aligned["requiredIdeaGroups"])
        self.assertEqual(aligned["childExpressionSource"], "independent_semantic_review_compiled")

    @patch("app.call_model_json")
    def test_subjective_semantic_reference_with_meifa_zhidao_is_accepted(self, model_call):
        planned = {
            "questionType": "emotional",
            "childIntent": "想知道恐龙的感受",
            "truthKernel": "我们无法知道恐龙的主观感受，因为恐龙已经灭绝。",
            "epistemicStatus": "subjective_unknown",
            "requiredIdeaGroups": [["没法知道"], ["恐龙已经不在了"]],
            "avoidClaims": ["恐龙一定觉得孤单"],
            "analogyPolicy": "omit",
            "childSafeAnswerNormal": "我们没法知道恐龙会不会孤单。因为恐龙已经不在了。",
            "conceptLabels": ["恐龙的感觉"],
        }
        semantic = {
            "passed": True,
            "factuallyGrounded": True,
            "epistemicAppropriate": True,
            "childReferencesAccurate": True,
            "minimalTruthSufficient": True,
            "materialOmission": False,
            "canonicalIdeaGroups": [["我们没法知道恐龙会不会孤单"], ["恐龙已经不在了"]],
            "canonicalIdeaGroupsByMode": {"normal": [["我们没法知道恐龙会不会孤单"], ["恐龙已经不在了"]]},
            "canonicalCausalChainByMode": {"normal": [["我们没法知道恐龙会不会孤单"], ["恐龙已经不在了"]]},
            "approvedChildAnswersByMode": {"normal": "我们没法知道恐龙会不会孤单。因为恐龙已经不在了。"},
            "optionalAdvancedDetails": [],
            "violations": [],
            "repairInstructions": [],
        }
        model_call.side_effect = [planned, semantic]
        plan = app.plan_question_with_model("恐龙会不会觉得孤单？", app.heuristic_question_plan("恐龙会不会觉得孤单？"), self.profile)
        self.assertEqual(plan["planStatus"], "ready")
        self.assertTrue(plan["planValidation"]["valid"])
        self.assertEqual(plan["causalChainByMode"], {})

    @patch("app.call_model_json")
    def test_semantic_repair_is_not_trusted_without_epistemic_boundary(self, model_call):
        model_call.return_value = {
            "passed": True,
            "factuallyGrounded": True,
            "epistemicAppropriate": True,
            "childReferencesAccurate": False,
            "minimalTruthSufficient": True,
            "materialOmission": False,
            "canonicalIdeaGroups": [["恐龙会不会孤单"]],
            "canonicalIdeaGroupsByMode": {"normal": [["恐龙会不会孤单"]]},
            "approvedChildAnswersByMode": {"normal": "恐龙可能会孤单。"},
            "optionalAdvancedDetails": [],
            "violations": [],
            "repairInstructions": [],
        }
        review = app.review_question_plan_with_model("恐龙会不会觉得孤单？", {
            "epistemicStatus": "subjective_unknown",
        }, self.profile, "normal")
        self.assertFalse(review["passed"])
        self.assertFalse(review["referenceRepairProvided"])
        self.assertTrue(any("认识边界" in item for item in review["violations"]))

    def test_long_approved_reference_keeps_natural_comma_relationship(self):
        text = "我们不知道恐龙会不会觉得孤单。因为恐龙已经不在了，没法知道它们心里的感觉。"
        fitted = app.fit_child_reference_sentences(text, 18)
        self.assertEqual(fitted, text)
        self.assertNotIn("已经不在了。没法知道", fitted)

    def test_mode_budget_compiler_merges_observation_without_losing_markers(self):
        text = (
            "你站在阳光下，脚边有个黑黑的影子。你往前走一步。"
            "那个黑影也跟着往前挪一步。因为身体挡住光，你动影子就动。"
        )
        markers = ["站在阳光下", "脚边有个黑黑的影子", "往前走一步", "黑影也跟着往前挪一步"]
        contract = app.child_level_alignment(self.profile, feedback="example")
        compiled = app.compile_child_reference_to_contract(text, contract, markers)
        self.assertLessEqual(len(app.split_sentences(compiled)), contract["maxSentences"])
        self.assertTrue(all(marker in compiled for marker in markers))
        self.assertIn("身体挡住光", compiled)
        self.assertTrue(all(
            len(sentence) <= contract["maxSentenceChars"]
            for sentence in app.split_sentences(compiled)
        ))

    def test_mode_budget_compiler_compacts_v20_shadow_reference(self):
        text = (
            "站在阳光下。身体挡住了太阳光。地上就有个黑黑的影子。"
            "你往前走。影子也会跟着你往前走。"
        )
        markers = ["站在阳光下", "身体挡住了太阳光", "地上就有个黑黑的影子", "你往前走"]
        selected = app.select_minimal_example_markers(text, markers)
        contract = app.child_level_alignment(self.profile, feedback="example")
        compiled = app.compile_child_reference_to_contract(text, contract, selected)
        sentences = app.split_sentences(compiled)
        self.assertEqual(selected, ["站在阳光下"])
        self.assertLessEqual(len(sentences), contract["maxSentences"])
        self.assertTrue(all(len(sentence) <= contract["maxSentenceChars"] for sentence in sentences))
        self.assertIn("身体挡住光", compiled)
        self.assertIn("有影子", compiled)
        self.assertIn("你走", compiled)
        self.assertIn("影子也跟着走", compiled)
        self.assertIn(selected[0], compiled)

    @patch("app.call_model_json")
    def test_semantic_example_overproduction_is_compiled_before_plan_validation(self, model_call):
        planned = {
            "questionType": "causal",
            "childIntent": "想知道影子为什么跟着走",
            "truthKernel": "身体挡住光，身体移动时影子也移动。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["身体挡住光"], ["有影子"], ["你动影子也动"]],
            "requiredIdeaGroupsByMode": {
                "normal": [["身体挡住光"], ["有影子"], ["你动影子也动"]],
                "example": [["身体挡住光"], ["有影子"], ["你动影子也动"]],
            },
            "causalChainByMode": {
                "normal": [["身体挡住光"], ["有影子"], ["你动影子也动"]],
                "example": [["身体挡住光"], ["有影子"], ["你动影子也动"]],
            },
            "avoidClaims": [],
            "analogyPolicy": "omit",
            "validatedExample": "你站在阳光下，脚边有个黑黑的影子。你往前走一步，黑影也跟着往前挪一步。",
            "validatedExampleMarkers": ["站在阳光下", "脚边有个黑黑的影子", "往前走一步", "黑影也跟着往前挪一步"],
            "validatedExampleType": "observation",
            "childSafeAnswerNormal": "身体挡住光，就有影子。你走，影子也走。",
            "childSafeAnswerExample": "身体挡住光，就有影子。你走，影子也走。你可以站在阳光下看看。",
            "conceptLabels": ["影子"],
        }
        semantic = {
            "passed": True,
            "factuallyGrounded": True,
            "epistemicAppropriate": True,
            "childReferencesAccurate": False,
            "minimalTruthSufficient": True,
            "materialOmission": False,
            "canonicalIdeaGroups": [["身体挡住光，就有影子"], ["你走，影子也走"]],
            "canonicalIdeaGroupsByMode": {
                "normal": [["身体挡住光，就有影子"], ["你走，影子也走"]],
                "example": [
                    ["你站在阳光下，脚边有个黑黑的影子"],
                    ["你往前走一步"],
                    ["那个黑影也跟着往前挪一步"],
                    ["因为身体挡住光，你动影子就动"],
                ],
            },
            "canonicalCausalChainByMode": {
                "normal": [["身体挡住光，就有影子"], ["你走，影子也走"]],
                "example": [
                    ["你站在阳光下，脚边有个黑黑的影子"],
                    ["你往前走一步"],
                    ["那个黑影也跟着往前挪一步"],
                    ["因为身体挡住光，你动影子就动"],
                ],
            },
            "approvedChildAnswersByMode": {
                "normal": "身体挡住光，就有影子。你走，影子也走。",
                "example": (
                    "你站在阳光下，脚边有个黑黑的影子。你往前走一步。"
                    "那个黑影也跟着往前挪一步。因为身体挡住光，你动影子就动。"
                ),
            },
            "optionalAdvancedDetails": [],
            "violations": [],
            "repairInstructions": [],
        }
        model_call.side_effect = [planned, semantic]
        plan = app.plan_question_with_model(
            "影子为什么会跟着我？",
            app.heuristic_question_plan("影子为什么会跟着我？"),
            self.profile,
            "example",
        )
        self.assertEqual(plan["planStatus"], "ready")
        self.assertEqual(len(plan["requiredIdeaGroupsByMode"]["example"]), 3)
        self.assertEqual(len(app.split_sentences(plan["childSafeAnswerExample"])), 3)
        self.assertEqual(len(plan["validatedExampleMarkers"]), 1)
        self.assertIn(plan["validatedExampleMarkers"][0], plan["childSafeAnswerExample"])
        self.assertTrue(plan["planValidation"]["valid"])

    def test_irrelevant_example_budget_does_not_block_normal_plan(self):
        plan = {
            "truthKernel": "水汽遇冷变成水滴，变重后落下来成为雨。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["水汽升上天"], ["变成小水滴"], ["掉下来变成雨"]],
            "requiredIdeaGroupsByMode": {
                "normal": [["水汽升上天"], ["变成小水滴"], ["掉下来变成雨"]],
                "example": [["水汽升上天"], ["变成小水滴"], ["掉下来变成雨"], ["冷盖子上有水珠"]],
            },
            "conceptLabels": ["下雨"],
            "analogyPolicy": "omit",
            "childSafeAnswerNormal": "水汽升上天。变成小水滴。掉下来变成雨。",
        }
        validation = app.validate_question_plan(plan, self.profile, "normal", "为什么会下雨？")
        self.assertTrue(validation["valid"])

    def test_age_five_allows_four_groups_for_necessary_causal_bridge(self):
        groups = [["水汽升空"], ["变成小水滴"], ["聚大变重"], ["落下来成雨"]]
        plan = {
            "truthKernel": "水变成水汽升空，水汽变成小水滴，水滴聚大变重后落下来成为雨。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": groups,
            "requiredIdeaGroupsByMode": {"normal": groups, "simpler": groups[1:]},
            "causalChainByMode": {"normal": groups, "simpler": groups[1:]},
            "conceptLabels": ["下雨"],
            "analogyPolicy": "omit",
            "childSafeAnswerNormal": "水汽升空。变成小水滴。水滴聚大变重，落下来成雨。",
            "childSafeAnswerSimpler": "水汽变成小水滴。水滴聚大变重。最后落下来成雨。",
        }
        validation = app.validate_question_plan(plan, self.profile, "simpler", "为什么会下雨？")
        self.assertTrue(validation["valid"])

    def test_simpler_feedback_uses_only_last_two_direct_fact_groups(self):
        groups = [["水汽升空"], ["变成小水滴"], ["聚大变重"], ["落下来成雨"]]
        plan = {
            "requiredIdeaGroups": groups,
            "requiredIdeaGroupsByMode": {"simpler": groups[1:]},
        }
        self.assertEqual(app.active_required_idea_groups(plan, "simpler"), groups[-2:])

    def test_example_feedback_uses_last_three_direct_fact_groups(self):
        groups = [["水汽升空"], ["变成小水滴"], ["聚大变重"], ["落下来成雨"]]
        plan = {
            "requiredIdeaGroups": groups,
            "requiredIdeaGroupsByMode": {"example": groups},
            "causalChainByMode": {"example": groups},
        }
        self.assertEqual(app.active_required_idea_groups(plan, "example"), groups[-3:])
        self.assertEqual(app.active_causal_chain(plan, "example"), groups[-3:])

    def test_curated_rain_scaffold_uses_short_example_chain(self):
        plan = app.apply_curated_fact_scaffold(app.heuristic_question_plan("为什么会下雨？"))
        self.assertEqual(
            plan["requiredIdeaGroupsByMode"]["example"],
            [["水汽遇冷变成小水滴"], ["小水滴聚大变重"], ["落下来成为雨"]],
        )
        self.assertEqual(plan["factBoundarySource"], "curated_science_anchor")

    def test_curated_scaffold_replaces_overlong_mode_specific_groups(self):
        plan = app.heuristic_question_plan("为什么会下雨？")
        plan.update({
            "requiredIdeaGroups": [["水汽升空"], ["小水滴变重"]],
            "requiredIdeaGroupsByMode": {
                "normal": [["水汽升空"], ["小水滴变重"]],
                "example": [
                    ["水汽遇到冷空气变成小水滴"],
                    ["就像烧开水时壶嘴冒出的白气一样"],
                    ["小水滴聚在一起变重落下来变成雨"],
                ],
            },
        })
        aligned = app.apply_curated_fact_scaffold(plan)
        self.assertEqual(
            aligned["requiredIdeaGroupsByMode"]["example"],
            [["水汽遇冷变成小水滴"], ["小水滴聚大变重"], ["落下来成为雨"]],
        )

    def test_simpler_feedback_still_rejects_reversed_direct_causal_chain(self):
        groups = [["水汽升空"], ["变成小水滴"], ["聚大变重"], ["落下来成雨"]]
        plan = {
            "planStatus": "ready",
            "feedbackMode": "simpler",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": groups,
            "requiredIdeaGroupsByMode": {"simpler": groups[1:]},
            "causalChainByMode": {"simpler": groups[1:]},
            "conceptLabels": ["下雨"],
            "analogyPolicy": "omit",
        }
        review = app.local_quality_review(
            candidate("雨先落下来成雨，然后小水滴才聚大变重。"),
            plan,
            app.child_level_alignment(self.profile, feedback="simpler"),
        )
        self.assertFalse(review["passed"])
        self.assertFalse(review["causalCoverage"]["ordered"])

    def test_example_feedback_accepts_complete_observable_example_without_exact_marker(self):
        plan = {
            "planStatus": "ready",
            "feedbackMode": "example",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [["身体挡住光"], ["后面变暗"]],
            "requiredIdeaGroupsByMode": {"example": [["身体挡住光"], ["后面变暗"]]},
            "causalChainByMode": {"example": [["身体挡住光"], ["后面变暗"]]},
            "validatedExampleMarkers": ["站在阳光下"],
            "conceptLabels": ["影子"],
            "analogyPolicy": "omit",
        }
        answer = candidate("比如拿手电筒照积木，你会看到墙上出现影子。身体挡住光，后面会变暗。这个例子说明，这就是影子出现的原因。")
        review = app.local_quality_review(
            answer,
            plan,
            app.child_level_alignment(self.profile, feedback="example"),
        )
        self.assertTrue(review["languageEvidence"]["observableExample"]["complete"])
        self.assertEqual(review["scores"]["feedbackFit"], 1.0)
        self.assertTrue(review["passed"])

    @patch("app.call_model_json")
    def test_semantic_review_preserves_four_age_five_causal_groups(self, model_call):
        groups = [["水汽升空"], ["变成小水滴"], ["聚大变重"], ["落下来成雨"]]
        model_call.return_value = {
            "passed": True,
            "factuallyGrounded": True,
            "epistemicAppropriate": True,
            "childReferencesAccurate": True,
            "minimalTruthSufficient": True,
            "materialOmission": False,
            "canonicalIdeaGroups": groups,
            "canonicalIdeaGroupsByMode": {"normal": groups},
            "canonicalCausalChainByMode": {"normal": groups},
            "approvedChildAnswersByMode": {
                "normal": "水汽升空。变成小水滴。聚大变重，落下来成雨。",
            },
            "optionalAdvancedDetails": [],
            "violations": [],
            "repairInstructions": [],
        }
        review = app.review_question_plan_with_model(
            "为什么会下雨？",
            {
                "epistemicStatus": "fact",
                "validatedExampleMarkers": [],
            },
            self.profile,
            "normal",
        )
        self.assertTrue(review["passed"])
        self.assertEqual(review["canonicalIdeaGroups"], groups)
        self.assertEqual(review["canonicalCausalChainByMode"]["normal"], groups)

    def test_age_five_rejects_four_groups_without_causal_bridge(self):
        groups = [["水汽升空"], ["变成小水滴"], ["聚大变重"], ["落下来成雨"]]
        plan = {
            "truthKernel": "水变成水汽升空，水汽变成小水滴，水滴聚大变重后落下来成为雨。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": groups,
            "requiredIdeaGroupsByMode": {"normal": groups},
            "conceptLabels": ["下雨"],
            "analogyPolicy": "omit",
            "childSafeAnswerNormal": "水汽升空。变成小水滴。水滴聚大变重，落下来成雨。",
        }
        validation = app.validate_question_plan(plan, self.profile, "normal", "为什么会下雨？")
        self.assertFalse(validation["valid"])
        self.assertTrue(any("1-3" in violation for violation in validation["violations"]))

    def test_age_five_plan_metadata_keeps_one_concept_label(self):
        fitted = app.fit_plan_metadata_to_contract(
            {"conceptLabels": ["下雨", "小水滴"]},
            self.profile,
            "simpler",
        )
        self.assertEqual(fitted["conceptLabels"], ["下雨"])

    @patch("app.call_model_json")
    def test_semantic_review_ignores_unused_simpler_budget_for_normal_request(self, model_call):
        model_call.return_value = {
            "passed": True,
            "factuallyGrounded": True,
            "epistemicAppropriate": True,
            "childReferencesAccurate": False,
            "minimalTruthSufficient": True,
            "materialOmission": False,
            "canonicalIdeaGroups": [["科学家认为"], ["大石头撞地球"], ["食物变少"]],
            "canonicalIdeaGroupsByMode": {
                "normal": [["科学家认为"], ["大石头撞地球"], ["食物变少"]],
                "simpler": [["大石头撞地球"], ["恐龙不见了"]],
            },
            "canonicalCausalChainByMode": {
                "normal": [["大石头撞地球"], ["食物变少"]],
            },
            "approvedChildAnswersByMode": {
                "normal": "科学家认为大石头撞地球。后来食物变少。",
                "simpler": "这是一个特别特别特别特别长的儿童参考答案",
            },
            "optionalAdvancedDetails": [],
            "violations": [],
            "repairInstructions": [],
        }
        review = app.review_question_plan_with_model(
            "恐龙为什么不见了？",
            {"epistemicStatus": "evidence_based"},
            {**self.profile, "age": 7},
            "normal",
        )
        self.assertTrue(review["passed"])
        self.assertNotIn("simpler", "；".join(review["violations"]))

    @patch("app.call_model_json")
    def test_semantic_review_can_set_mode_specific_minimum_truth(self, model_call):
        model_call.return_value = {
            "passed": True,
            "factuallyGrounded": True,
            "epistemicAppropriate": True,
            "childReferencesAccurate": True,
            "minimalTruthSufficient": True,
            "materialOmission": False,
            "canonicalIdeaGroups": [["水汽升上去"], ["变成小水滴"], ["落成雨"]],
            "canonicalIdeaGroupsByMode": {
                "normal": [["水汽升上去"], ["变成小水滴"], ["落成雨"]],
                "simpler": [["小水滴变大"], ["落成雨"]],
            },
            "optionalAdvancedDetails": ["凝结核"],
            "violations": [],
            "repairInstructions": [],
        }
        review = app.review_question_plan_with_model("为什么会下雨？", {
            "truthKernel": "水汽遇冷成为小水滴，聚大后落下形成雨。",
            "epistemicStatus": "fact",
            "childSafeAnswerNormal": "水汽升上去。变成小水滴，最后落成雨。",
            "childSafeAnswerSimpler": "云里小水滴变大。太重了，落成雨。",
        }, self.profile, "simpler")
        aligned = app.apply_semantic_idea_groups({}, review)
        self.assertTrue(review["passed"])
        self.assertEqual(aligned["requiredIdeaGroupsByMode"]["simpler"], [["小水滴变大"], ["落成雨"]])

    @patch("app.call_model_json")
    def test_semantic_review_does_not_require_optional_advanced_detail(self, model_call):
        model_call.return_value = {
            "passed": True,
            "factuallyGrounded": True,
            "epistemicAppropriate": True,
            "childReferencesAccurate": True,
            "minimalTruthSufficient": True,
            "materialOmission": False,
            "canonicalIdeaGroups": [["小水滴"], ["落下来"]],
            "optionalAdvancedDetails": ["凝结核", "空气托举阈值"],
            "violations": [],
            "repairInstructions": [],
        }
        review = app.review_question_plan_with_model("为什么会下雨？", {
            "truthKernel": "云里的小水滴聚大后落下来形成雨。",
            "epistemicStatus": "fact",
            "childSafeAnswerNormal": "云里有小水滴。它们变大后落下来。",
        }, self.profile, "normal")
        self.assertTrue(review["passed"])
        self.assertFalse(review["materialOmission"])
        self.assertIn("凝结核", review["optionalAdvancedDetails"])

    @patch("app.call_model_json")
    def test_parent_confirmation_fallback_is_safe_but_not_aligned_pass(self, model_call):
        model_call.return_value = {
            "truthKernel": "水从云里落下来。",
            "epistemicStatus": "fact",
            "requiredIdeaGroups": [],
            "conceptLabels": [],
        }
        _, workflow = app.run_child_alignment_workflow(self.profile, "为什么会下雨？", "normal", [])
        self.assertEqual(workflow["answerSource"], "quality_fallback")
        self.assertFalse(workflow["quality"]["passed"])
        self.assertFalse(workflow["quality"]["deliveryValidated"])
        self.assertTrue(workflow["quality"]["fallbackSafe"])
        self.assertFalse(workflow["deliveryDecision"]["deliveryValidated"])

    @patch("app.call_model_json")
    def test_malformed_child_schema_never_delivers_mock_fallback(self, model_call):
        answer, workflow = app.run_child_alignment_workflow(
            self.profile,
            "为什么月亮好像跟着我走？",
            "normal",
            [],
        )
        self.assertNotEqual(workflow["answerSource"], "mock_fallback")
        self.assertEqual(workflow["answerSource"], "quality_fallback")
        self.assertFalse(workflow["quality"]["passed"])
        self.assertNotIn("月亮离我们很远", answer["displayText"])
        self.assertEqual(model_call.call_count, 2)

    def test_mixed_correct_and_contradictory_teachback_is_not_mastery(self):
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        assessment = app.teachback_evidence(
            "月亮很远，位置变化很小，但月亮喜欢我所以追着我。",
            plan,
        )
        self.assertFalse(assessment["correct"])
        self.assertEqual(assessment["outcome"], "contradicted")
        self.assertTrue(assessment["contradiction"])

    @patch("app.call_model_json", side_effect=RuntimeError("reviewer unavailable"))
    def test_reviewer_unavailable_cannot_create_positive_mastery(self, _model_call):
        local = app.teachback_evidence(
            "月亮很远，所以位置变化很小。",
            app.heuristic_question_plan("为什么月亮好像跟着我走？"),
        )
        self.assertTrue(local["localPassed"])
        reviewed = app.review_teachback_with_model(
            "月亮很远，所以位置变化很小。",
            app.heuristic_question_plan("为什么月亮好像跟着我走？"),
            local,
        )
        self.assertFalse(reviewed["correct"])
        self.assertFalse(reviewed["reviewed"])
        self.assertEqual(reviewed["outcome"], "unverified")

    def test_one_correct_teachback_is_not_known(self):
        db = app.seed_db()
        updates = [{"concept": "远近与视觉位置", "evidence": "", "effectiveAnalogy": "", "truthKernel": ""}]
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        changed, _ = app.upsert_memory(db, app.DEFAULT_CHILD_ID, updates, ["u", "a"], "teachback", "月亮很远，所以位置变化很小。", plan, reviewed_correct_teachback())
        self.assertNotEqual(changed[0]["status"], "known")
        self.assertEqual(changed[0]["reviewedTeachbackCorrectCount"], 1)

    def test_two_correct_teachbacks_can_be_known(self):
        db = app.seed_db()
        db["memoryItems"]["mastery"] = {
            "id": "mastery", "childId": app.DEFAULT_CHILD_ID, "type": "cognitive",
            "concept": "远近与视觉位置", "status": "learning", "confidence": 0.55,
            "evidence": "", "effectiveAnalogy": "", "sourceMessageIds": [], "history": [],
            "parentVerified": False, "teachbackCorrectCount": 0,
            "reviewedTeachbackCorrectCount": 0, "confusionCount": 0,
        }
        updates = [{"concept": "远近与视觉位置", "evidence": "", "effectiveAnalogy": "", "truthKernel": ""}]
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        for index in range(2):
            changed, _ = app.upsert_memory(db, app.DEFAULT_CHILD_ID, updates, [f"u{index}", f"a{index}"], "teachback", "月亮很远，所以位置变化很小。", plan, reviewed_correct_teachback())
        self.assertEqual(changed[0]["status"], "known")
        self.assertTrue(app.memory_card_is_trusted(changed[0]))

    def test_incorrect_teachback_needs_review(self):
        db = app.seed_db()
        updates = [{"concept": "远近与视觉位置", "evidence": "", "effectiveAnalogy": "", "truthKernel": ""}]
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？")
        changed, evidence = app.upsert_memory(db, app.DEFAULT_CHILD_ID, updates, ["u", "a"], "teachback", "月亮喜欢我，所以追着我。", plan, contradicted_teachback())
        self.assertEqual(changed[0]["status"], "needs_review")
        self.assertFalse(evidence[0]["masteryCorrect"])
        self.assertEqual(evidence[0]["assessmentOutcome"], "contradicted")

    @patch("app.teachback_evidence", side_effect=AssertionError("memory writer must not recompute"))
    def test_memory_writer_uses_provided_reviewed_assessment(self, _teachback_evidence):
        db = app.seed_db()
        updates = [{"concept": "远近与视觉位置", "evidence": "", "effectiveAnalogy": "", "truthKernel": ""}]
        changed, evidence = app.upsert_memory(
            db,
            app.DEFAULT_CHILD_ID,
            updates,
            ["u", "a"],
            "teachback",
            "月亮很远，所以位置变化很小。",
            app.heuristic_question_plan("为什么月亮好像跟着我走？"),
            reviewed_correct_teachback(),
        )
        self.assertEqual(changed[0]["reviewedTeachbackCorrectCount"], 1)
        self.assertEqual(evidence[0]["assessmentSource"], "local+parent_analysis")

    def test_unreviewed_local_positive_teachback_cannot_become_known(self):
        db = app.seed_db()
        db["memoryItems"]["mastery"] = {
            "id": "mastery", "childId": app.DEFAULT_CHILD_ID, "type": "cognitive",
            "concept": "远近与视觉位置", "status": "learning", "confidence": 0.8,
            "evidence": "", "effectiveAnalogy": "", "sourceMessageIds": [], "history": [],
            "parentVerified": False, "reviewedTeachbackCorrectCount": 1, "confusionCount": 0,
        }
        updates = [{"concept": "远近与视觉位置", "evidence": "", "effectiveAnalogy": "", "truthKernel": ""}]
        changed, evidence = app.upsert_memory(
            db,
            app.DEFAULT_CHILD_ID,
            updates,
            ["u", "a"],
            "teachback",
            "月亮很远，所以位置变化很小。",
            {},
            unreviewed_positive_teachback(),
        )
        self.assertEqual(changed[0]["reviewedTeachbackCorrectCount"], 1)
        self.assertNotEqual(changed[0]["status"], "known")
        self.assertEqual(evidence[0]["evidenceType"], "mastery_unverified")

    def test_unverified_teachback_does_not_change_confidence(self):
        db = app.seed_db()
        db["memoryItems"]["learning"] = {
            "id": "learning", "childId": app.DEFAULT_CHILD_ID, "type": "cognitive",
            "concept": "远近与视觉位置", "status": "learning", "confidence": 0.61,
            "evidence": "", "effectiveAnalogy": "", "sourceMessageIds": [], "history": [],
            "parentVerified": False, "reviewedTeachbackCorrectCount": 0, "confusionCount": 0,
        }
        assessment = {**unreviewed_positive_teachback(), "correct": False, "outcome": "unverified", "assessmentAvailable": False}
        changed, _ = app.upsert_memory(
            db,
            app.DEFAULT_CHILD_ID,
            [{"concept": "远近与视觉位置"}],
            ["u", "a"],
            "teachback",
            "月亮很远。",
            {},
            assessment,
        )
        self.assertEqual(changed[0]["confidence"], 0.61)
        self.assertEqual(changed[0]["status"], "learning")

    def test_only_known_cards_enter_trusted_child_context(self):
        cards = [
            {"concept": "待复习", "status": "needs_review", "confidence": 0.9, "parentVerified": True, "reviewedTeachbackCorrectCount": 3},
            {"concept": "学习中", "status": "learning", "confidence": 0.9, "parentVerified": True, "reviewedTeachbackCorrectCount": 3},
            {"concept": "已掌握", "status": "known", "confidence": 0.8, "parentVerified": False, "reviewedTeachbackCorrectCount": 2},
        ]
        contract = app.child_level_alignment(self.profile, related_cards=cards)
        self.assertEqual(contract["knownConcepts"], ["已掌握"])
        self.assertFalse(app.memory_card_is_trusted(cards[0]))
        self.assertFalse(app.memory_card_is_trusted(cards[1]))
        self.assertTrue(app.memory_card_is_trusted(cards[2]))

    def test_plan_concept_labels_drive_cognitive_memory(self):
        plan = {"conceptLabels": ["天空颜色"], "truthKernel": "蓝色光更容易散到四周。", "analogyPolicy": "omit"}
        updates = app.derive_memory_updates(
            "为什么天空是蓝色的？",
            candidate("阳光里的蓝色光更容易散到四周。"),
            plan,
        )
        self.assertEqual([item["concept"] for item in updates], ["天空颜色"])

    def test_example_feedback_only_updates_preference(self):
        db = app.seed_db()
        db["memoryItems"]["moon"] = {
            "id": "moon", "childId": app.DEFAULT_CHILD_ID, "type": "cognitive",
            "concept": "月亮和天空", "status": "candidate", "confidence": 0.38,
            "history": [], "sourceMessageIds": [], "updatedAt": app.now_iso(),
        }
        before = db["memoryItems"]["moon"]["confidence"]
        updates = [{"concept": "月亮和天空", "evidence": "", "effectiveAnalogy": "", "truthKernel": ""}]
        app.upsert_memory(db, app.DEFAULT_CHILD_ID, updates, ["u", "a"], "example", "为什么月亮跟着我？", {})
        self.assertLessEqual(db["memoryItems"]["moon"]["confidence"], before + 0.02)
        self.assertTrue(any(item.get("type") == "preference" and item.get("concept") == "解释偏好：生活例子" for item in db["memoryItems"].values()))

    def test_danger_and_privacy_are_blocked(self):
        self.assertEqual(app.safety_check("怎么用打火机点纸玩？")["level"], "blocked")
        self.assertEqual(app.safety_check("我把家里地址告诉你好吗？")["category"], "privacy")

    @patch("app.call_model_json")
    def test_workflow_exposes_source_and_quality(self, model_call):
        model_call.return_value = candidate(
            "月亮其实没有在追着你走。它离我们非常远，所以你走几步时，看月亮的方向只会发生很小的变化。你的眼睛很难看出这点变化，就会觉得月亮一直待在差不多的位置。"
        )
        answer, workflow = app.run_child_alignment_workflow(self.profile, "为什么月亮好像跟着我走？", "normal", [])
        self.assertEqual(workflow["answerSource"], "real")
        self.assertEqual(workflow["generationSource"], "child_answer_model")
        self.assertGreaterEqual(len(answer["displayText"]), 42)
        self.assertGreaterEqual(len(app.split_sentences(answer["displayText"])), 3)
        model_call.assert_called_once()
        self.assertIn("quality", workflow)
        self.assertIn("questionPlan", workflow)
        self.assertIn("deliveryDecision", workflow)
        self.assertEqual(workflow["quality"]["qualityAttemptCount"], 1)
        self.assertEqual(workflow["quality"]["repairCount"], 0)

    @patch("app.call_model_json")
    def test_quality_gate_passes_on_third_candidate_with_prior_feedback(self, model_call):
        bad_first = candidate("月亮在追着你。")
        bad_second = candidate("月亮没有追你，因为它很远。")
        valid = candidate(
            "月亮其实没有在追着你走。它离我们非常远，所以你走几步时，看月亮的方向只会发生很小的变化。"
            "你的眼睛很难看出这点变化，就会觉得月亮一直待在差不多的位置。"
        )
        model_call.side_effect = [bad_first, bad_second, valid]

        answer, workflow = app.run_child_alignment_workflow(
            self.profile,
            "为什么月亮好像跟着我走？",
            "normal",
            [],
        )

        self.assertEqual(answer["displayText"], valid["displayText"])
        self.assertEqual(workflow["answerSource"], "real")
        self.assertTrue(workflow["deliveryDecision"]["deliveryValidated"])
        self.assertEqual(workflow["quality"]["qualityAttemptCount"], 3)
        self.assertEqual(workflow["quality"]["repairCount"], 2)
        self.assertEqual(model_call.call_count, 3)
        third_payload = model_call.call_args_list[2].args[2]
        self.assertEqual(third_payload["qualityAttempt"], 3)
        self.assertEqual(third_payload["maximumQualityAttempts"], 10)
        self.assertEqual(third_payload["candidateToRepair"]["displayText"], bad_second["displayText"])
        self.assertTrue(third_payload["qualityFailures"])
        self.assertEqual([item["attempt"] for item in third_payload["previousQualityAttempts"]], [1, 2])

    @patch("app.call_model_json")
    def test_observe_repair_receives_specific_failed_structure_evidence(self, model_call):
        explanation = (
            "月亮没有追着你走。它离我们很远，所以你走几步时，看月亮的方向变化很小。"
            "你的眼睛就会觉得它还在差不多的位置。现在可以请家长陪你验证这个现象。"
        )
        incomplete = candidate(explanation)
        incomplete["safeExperiment"] = (
            "家长陪同：请家长全程陪着。准备：晚上待在室内窗边。"
            "一起做：向左走两步再停下。家长可以问：月亮真的追过来了吗？"
            "安全提醒：不要打开窗，也不要独自外出。"
        )
        complete = candidate(explanation)
        complete["safeExperiment"] = (
            "家长陪同：请家长全程陪着。准备：晚上待在室内窗边。"
            "一起做：向左走两步再停下。重点观察：月亮和窗框的位置有没有明显变化。"
            "家长可以问：月亮真的追过来了吗？安全提醒：不要打开窗，也不要独自外出。"
        )
        model_call.side_effect = [incomplete, complete]

        answer, workflow = app.run_child_alignment_workflow(
            self.profile,
            "为什么月亮好像跟着我走？",
            "normal",
            [],
            activity_mode="observe",
        )

        self.assertEqual(answer["safeExperiment"], complete["safeExperiment"])
        self.assertTrue(workflow["deliveryDecision"]["deliveryValidated"])
        repair_payload = model_call.call_args_list[1].args[2]
        self.assertFalse(repair_payload["modeEvidence"]["phenomenon"])
        self.assertTrue(repair_payload["modeEvidence"]["adultCompanion"])
        self.assertIn("重点观察：", repair_payload["repairRules"]["observeSafeExperimentRule"])

    @patch("app.call_model_json")
    def test_quality_gate_stops_after_ten_rejected_candidates(self, model_call):
        bad = candidate("蓝色积木到处跑。", analogy="像蓝色积木")
        model_call.return_value = bad

        _, workflow = app.run_child_alignment_workflow(
            self.profile,
            "为什么天空是蓝色的？",
            "normal",
            [],
        )

        self.assertEqual(workflow["answerSource"], "quality_fallback")
        self.assertFalse(workflow["deliveryDecision"]["deliveryValidated"])
        self.assertEqual(workflow["quality"]["qualityAttemptCount"], 10)
        self.assertEqual(workflow["quality"]["repairCount"], 9)
        self.assertEqual(workflow["quality"]["maximumQualityAttempts"], 10)
        self.assertEqual(len(workflow["quality"]["qualityAttemptHistory"]), 10)
        self.assertEqual(model_call.call_count, 10)

    @patch("app.call_model_json")
    def test_feedback_quality_gate_stops_after_four_rejected_candidates(self, model_call):
        model_call.return_value = candidate("蓝色积木到处跑。", analogy="像蓝色积木")

        _, workflow = app.run_child_alignment_workflow(
            self.profile,
            "为什么天空是蓝色的？",
            "example",
            [],
        )

        self.assertEqual(workflow["answerSource"], "quality_fallback")
        self.assertFalse(workflow["deliveryDecision"]["deliveryValidated"])
        self.assertEqual(workflow["quality"]["qualityAttemptCount"], 4)
        self.assertEqual(workflow["quality"]["repairCount"], 3)
        self.assertEqual(workflow["quality"]["maximumQualityAttempts"], 4)
        self.assertEqual(model_call.call_count, 4)

    @patch("app.call_model_json")
    def test_failed_model_never_uses_precomposed_answer_fallback(self, model_call):
        bad = candidate("蓝色积木到处跑。", analogy="像蓝色积木")
        model_call.return_value = bad
        answer, workflow = app.run_child_alignment_workflow(self.profile, "为什么天空是蓝色的？", "normal", [])
        self.assertEqual(workflow["answerSource"], "quality_fallback")
        self.assertFalse(workflow["quality"]["passed"])
        self.assertNotIn("蓝色光", answer["displayText"])
        self.assertIn("未使用预写答案替代", workflow["quality"]["violations"][0])
        self.assertTrue(workflow["deliveryDecision"]["fallbackUsed"])
        self.assertEqual(workflow["quality"]["qualityAttemptCount"], 10)

    @patch("app.call_model_json")
    def test_rejected_model_repair_cannot_be_delivered_as_validated(self, model_call):
        bad = candidate("蓝色积木到处跑。", analogy="像蓝色积木")
        model_call.return_value = bad
        _, workflow = app.run_child_alignment_workflow(self.profile, "为什么天空是蓝色的？", "normal", [])
        self.assertEqual(workflow["answerSource"], "quality_fallback")
        self.assertFalse(workflow["quality"]["passed"])
        self.assertFalse(workflow["deliveryDecision"]["deliveryValidated"])

    def test_required_independent_review_fails_closed_when_unavailable(self):
        local = {
            "passed": True,
            "overall": 1.0,
            "scores": {"safety": 1.0},
            "violations": [],
        }
        merged = app.merge_quality_reviews(local, None, require_model_review=True)
        self.assertFalse(merged["passed"])
        self.assertTrue(any("独立回答审核不可用" in violation for violation in merged["violations"]))

    def test_unavailable_analogy_reviewer_can_soft_pass_when_local_gate_passes(self):
        local = {
            "passed": True,
            "overall": 1.0,
            "scores": {"safety": 1.0},
            "violations": [],
        }
        plan = {"epistemicStatus": "fact", "planStatus": "ready"}
        merged = app.merge_quality_reviews(local, None, plan=plan, require_model_review=True)
        self.assertTrue(merged["passed"])
        self.assertTrue(merged["softPassed"])
        self.assertEqual(merged["reviewSource"], "local_soft_pass")

    @patch("app.call_model_json")
    def test_soft_only_workflow_repairs_at_most_once(self, model_call):
        short_detail = candidate(
            "月亮没有真的追着你。它离我们很远，所以你走几步时，看它的方向变化很小。"
            "你的眼睛不容易发现这点变化，就觉得月亮还在差不多的位置。"
        )
        model_call.return_value = short_detail

        answer, workflow = app.run_child_alignment_workflow(
            self.profile,
            "为什么月亮好像跟着我走？",
            "normal",
            [],
            activity_mode="detail",
        )

        self.assertEqual(answer["displayText"], short_detail["displayText"])
        self.assertTrue(workflow["deliveryDecision"]["deliveryValidated"])
        self.assertEqual(workflow["quality"]["qualityAttemptCount"], 2)
        self.assertEqual(workflow["quality"]["repairCount"], 1)
        self.assertEqual(workflow["quality"]["reviewSource"], "local_soft_pass")
        self.assertEqual(model_call.call_count, 2)

    def test_memory_does_not_learn_unvetted_analogy(self):
        plan = app.heuristic_question_plan("为什么天空是蓝色的？")
        answer = candidate("阳光里有蓝色光。它更容易散到四周。", analogy="像积木")
        updates = app.derive_memory_updates("为什么天空是蓝色的？", answer, plan)
        self.assertTrue(updates)
        self.assertTrue(all(update["effectiveAnalogy"] == "" for update in updates))

    def test_feedback_strategy_is_versioned_and_public(self):
        strategy = app.turn_strategy("observe", "confused")
        self.assertEqual(strategy["id"], "explain.reframe.v1")
        self.assertEqual(strategy["activityMode"], "observe")
        self.assertEqual(strategy["scope"], "public_explanation_strategy")
        self.assertNotIn("answer", strategy)

    def test_structured_turn_trace_is_auditable_without_chain_of_thought(self):
        workflow = {
            "quality": {"passed": True},
            "deliveryDecision": {"deliveryValidated": True},
            "latencyBreakdownSeconds": {
                "planning": 0.2,
                "answerPreparation": 0.3,
                "initialAnswerReview": 0.1,
            },
        }
        trace = app.structured_turn_trace(
            {"requestPreparation": 0.01, "memoryAndTrace": 0.02, "persistence": 0.01, "serverTotal": 0.64},
            workflow,
            {"level": "safe", "category": "none"},
            app.turn_strategy("ask", "normal"),
            [{"concept": "月亮"}],
            [],
        )
        self.assertEqual(trace["schemaVersion"], "1.0")
        self.assertFalse(trace["containsChainOfThought"])
        self.assertEqual([stage["stage"] for stage in trace["stages"]], ["safety", "understanding", "composing", "checking", "recording"])
        self.assertTrue(all("summary" in stage and "failureCode" in stage for stage in trace["stages"]))

    def test_agent_components_inherit_existing_model_configuration(self):
        config = {
            "provider": {"type": "openai_compatible"},
            "models": {
                "child_answer": {"model_id": "child-model", "timeout_seconds": 8},
                "parent_analysis": {"model_id": "parent-model", "timeout_seconds": 6},
            },
        }
        with patch.object(app, "load_model_config", return_value=config):
            self.assertEqual(app.model_component_config("child_tutor")["model"]["model_id"], "child-model")
            self.assertEqual(app.model_component_config("question_planner")["model"]["model_id"], "parent-model")
            self.assertEqual(app.model_component_config("quality_reviewer")["model"]["model_id"], "parent-model")
            self.assertEqual(app.model_component_config("teachback_reviewer")["model"]["model_id"], "parent-model")

    def test_agent_orchestration_exposes_roles_without_credentials_or_chain_of_thought(self):
        workflow = {
            "answerSource": "real",
            "questionPlan": {"planStatus": "ready"},
            "quality": {"passed": True, "reviewSource": "local_quality_gate"},
            "deliveryDecision": {"deliveryValidated": True},
            "latencyBreakdownSeconds": {"planning": 0.2, "answerPreparation": 0.3, "initialAnswerReview": 0.1},
        }
        orchestration = app.agent_orchestration(workflow, {"level": "safe"}, "detail", "normal", [{"concept": "月亮"}])
        serialized = str(orchestration).lower()
        self.assertEqual(orchestration["architecture"], "supervisor_routed_multi_agent")
        self.assertEqual(orchestration["mode"]["label"], "详细解答")
        self.assertFalse(orchestration["containsChainOfThought"])
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("base_url", serialized)
        statuses = {agent["id"]: agent["status"] for agent in orchestration["agents"]}
        self.assertEqual(statuses["child_tutor"], "completed")
        self.assertEqual(statuses["memory_steward"], "completed")
        self.assertEqual(statuses["parent_coach"], "deferred")

    def test_agent_orchestration_skips_memory_when_delivery_fails(self):
        workflow = {
            "answerSource": "quality_fallback",
            "questionPlan": {"planStatus": "invalid"},
            "quality": {"passed": False},
            "deliveryDecision": {"deliveryValidated": False},
            "latencyBreakdownSeconds": {},
        }
        orchestration = app.agent_orchestration(workflow, {"level": "safe"}, "ask", "normal", [])
        statuses = {agent["id"]: agent["status"] for agent in orchestration["agents"]}
        self.assertEqual(statuses["learning_planner"], "failed")
        self.assertEqual(statuses["child_tutor"], "failed")
        self.assertEqual(statuses["memory_steward"], "skipped")

    def test_public_agent_system_treats_speech_as_tool(self):
        system = app.public_agent_system()
        self.assertFalse(system["containsChainOfThought"])
        self.assertIn("child_tutor", {agent["id"] for agent in system["agents"]})
        self.assertIn("speech_recognition", {tool["id"] for tool in system["tools"]})
        self.assertNotIn("speech_recognition", {agent["id"] for agent in system["agents"]})

    def test_runtime_audit_redacts_credentials_and_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "runtime-events.jsonl"
            with patch.object(app, "LOG_DIR", Path(temp_dir)), patch.object(app, "RUNTIME_LOG_PATH", log_path):
                app.write_runtime_event(
                    "request.error",
                    "backend",
                    "failed",
                    trace_id="trace_test",
                    component="chat",
                    details={
                        "apiKey": "secret-key",
                        "baseUrl": "https://example.invalid/v1",
                        "userText": "孩子的真实问题",
                        "displayText": "模型的真实回答",
                        "audioData": "data:audio/wav;base64,AAAA",
                        "answerChars": 42,
                    },
                )
            event = json.loads(log_path.read_text(encoding="utf-8"))
            serialized = json.dumps(event, ensure_ascii=False)
            self.assertNotIn("secret-key", serialized)
            self.assertNotIn("example.invalid", serialized)
            self.assertNotIn("孩子的真实问题", serialized)
            self.assertNotIn("模型的真实回答", serialized)
            self.assertNotIn("base64,AAAA", serialized)
            self.assertEqual(event["details"]["answerChars"], 42)

    def test_parent_visualization_exposes_exactly_the_four_answer_modes(self):
        visualization = app.memory_visualization(app.seed_db(), app.DEFAULT_CHILD_ID)
        self.assertEqual(
            [item["mode"] for item in visualization["modePlaybook"]],
            ["ask", "detail", "story", "observe"],
        )
        self.assertIn("agentSystem", visualization)

    def test_memory_visualization_uses_real_dated_evidence_for_trend(self):
        db = app.seed_db()
        db["messages"] = {
            "u1": {"id": "u1", "childId": app.DEFAULT_CHILD_ID, "role": "user", "text": "为什么下雨？", "createdAt": "2026-07-20T10:00:00", "activityMode": "ask", "feedbackMode": "normal"},
            "a1": {"id": "a1", "childId": app.DEFAULT_CHILD_ID, "role": "assistant", "text": "回答一", "createdAt": "2026-07-20T10:00:01", "deliveryDecision": {"deliveryValidated": True}, "quality": {"qualityAttemptCount": 1}},
            "u2": {"id": "u2", "childId": app.DEFAULT_CHILD_ID, "role": "user", "text": "为什么小水滴聚大变重后会落下来？", "createdAt": "2026-07-22T10:00:00", "activityMode": "detail", "feedbackMode": "why"},
            "a2": {"id": "a2", "childId": app.DEFAULT_CHILD_ID, "role": "assistant", "text": "回答二", "createdAt": "2026-07-22T10:00:01", "deliveryDecision": {"deliveryValidated": False}, "quality": {"qualityAttemptCount": 4}},
        }
        db["memoryItems"] = {
            "m1": {"id": "m1", "childId": app.DEFAULT_CHILD_ID, "type": "cognitive", "concept": "下雨", "status": "learning", "confidence": 0.45, "updatedAt": "2026-07-20T10:01:00"},
            "m2": {"id": "m2", "childId": app.DEFAULT_CHILD_ID, "type": "cognitive", "concept": "水滴", "status": "known", "confidence": 0.9, "parentVerified": True, "updatedAt": "2026-07-22T10:01:00"},
        }
        trend = app.memory_visualization(db, app.DEFAULT_CHILD_ID)["evolutionTrend"]
        self.assertEqual([point["date"] for point in trend], ["2026-07-20", "2026-07-22"])
        self.assertEqual(trend[0]["understandingScore"], 52)
        self.assertEqual(trend[0]["questionDifficulty"], 38)
        self.assertEqual(trend[0]["answerAdaptation"], 90)
        self.assertEqual(trend[0]["independenceScore"], 0)
        self.assertEqual(trend[1]["understandingScore"], 72)
        self.assertEqual(trend[1]["questionDifficulty"], 70)
        self.assertEqual(trend[1]["answerAdaptation"], 19)
        self.assertEqual(trend[1]["independenceScore"], 53)
        self.assertNotEqual(trend[0]["questionDifficulty"], trend[1]["questionDifficulty"])
        self.assertNotEqual(trend[0]["answerAdaptation"], trend[1]["answerAdaptation"])
        for point in trend:
            for key in ("understandingScore", "questionDifficulty", "answerAdaptation", "independenceScore"):
                self.assertGreaterEqual(point[key], 0)
                self.assertLessEqual(point[key], 100)

    def test_parent_feed_cards_quote_real_child_messages_and_mark_inference(self):
        db = app.seed_db()
        db["messages"] = {
            "u1": {"id": "u1", "childId": app.DEFAULT_CHILD_ID, "role": "user", "text": "恐龙都死掉了，看了会难过。", "createdAt": "2026-07-01T10:00:00"},
            "u2": {"id": "u2", "childId": app.DEFAULT_CHILD_ID, "role": "user", "text": "没人记得是不是就真的消失了？", "createdAt": "2026-07-20T10:00:00"},
        }
        db["memoryItems"]["association"] = {
            "id": "association", "childId": app.DEFAULT_CHILD_ID, "type": "association", "status": "active",
            "concept": "关于消失的跨期关切", "confidence": 0.62,
            "evidence": "两段表达可能相关，需要家长核对。", "sourceMessageIds": ["u1", "u2"],
            "updatedAt": "2026-07-20T10:01:00",
        }
        feed = app.build_parent_feed_cards(db, app.DEFAULT_CHILD_ID)
        association = next(card for card in feed if card["type"] == "association")
        self.assertEqual([quote["sourceId"] for quote in association["sourceQuotes"]], ["u1", "u2"])
        self.assertEqual(association["evidenceLevel"], "待核对的推测")
        self.assertIn("不是心理诊断", association["disclaimer"])

    def test_curiosity_card_quotes_only_its_strongest_topic(self):
        db = app.seed_db()
        db["messages"] = {
            "moon_1": {"id": "moon_1", "childId": app.DEFAULT_CHILD_ID, "role": "user", "text": "月亮为什么会跟着我？", "createdAt": "2026-07-01T10:00:00"},
            "moon_2": {"id": "moon_2", "childId": app.DEFAULT_CHILD_ID, "role": "user", "text": "月亮为什么有时像小船？", "createdAt": "2026-07-01T10:01:00"},
            "moon_3": {"id": "moon_3", "childId": app.DEFAULT_CHILD_ID, "role": "user", "text": "星星为什么会眨眼？", "createdAt": "2026-07-01T10:02:00"},
            "shadow": {"id": "shadow", "childId": app.DEFAULT_CHILD_ID, "role": "user", "text": "下午的影子为什么变长？", "createdAt": "2026-07-01T10:03:00"},
            "plant": {"id": "plant", "childId": app.DEFAULT_CHILD_ID, "role": "user", "text": "阳台的小花怎么喝水？", "createdAt": "2026-07-01T10:04:00"},
        }
        db["memoryItems"] = {}

        curiosity = next(card for card in app.build_parent_feed_cards(db, app.DEFAULT_CHILD_ID) if card["type"] == "curiosity")

        self.assertEqual(curiosity["title"], "最近在持续探索：太空/月亮")
        self.assertEqual([quote["sourceId"] for quote in curiosity["sourceQuotes"]], ["moon_2", "moon_3"])
        self.assertTrue(all("月亮" in quote["text"] or "星" in quote["text"] for quote in curiosity["sourceQuotes"]))

    def test_parent_feed_cold_start_does_not_invent_child_quotes(self):
        db = app.seed_db()
        db["messages"] = {}
        db["memoryItems"] = {}
        feed = app.build_parent_feed_cards(db, app.DEFAULT_CHILD_ID)
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["type"], "onboarding")
        self.assertEqual(feed[0]["sourceQuotes"], [])

    def test_showcase_import_is_isolated_and_idempotent(self):
        db = app.seed_db()
        db["messages"]["live_message"] = {"id": "live_message", "childId": "child_live", "role": "user", "text": "普通用户的问题"}
        first = app.import_showcase_child(db)
        second = app.import_showcase_child(db)
        self.assertEqual(first["childId"], app.SHOWCASE_CHILD_ID)
        self.assertEqual(second["counts"]["messages"], 2016)
        self.assertEqual(db["profiles"][app.SHOWCASE_CHILD_ID]["nickname"], "小满")
        self.assertEqual(len([item for item in db["messages"].values() if item.get("childId") == app.SHOWCASE_CHILD_ID]), 2016)
        self.assertIn("live_message", db["messages"])
        self.assertEqual(db["showcases"][app.SHOWCASE_CHILD_ID]["dialogueRounds"], 1008)
        self.assertEqual(len([item for item in db["memoryItems"].values() if item.get("childId") == app.SHOWCASE_CHILD_ID and item.get("type") == "cognitive"]), 18)
        self.assertEqual(len([item for item in db["memoryItems"].values() if item.get("childId") == app.SHOWCASE_CHILD_ID and item.get("type") == "preference"]), 6)
        self.assertEqual(len([item for item in db["safetyEvents"].values() if item.get("childId") == app.SHOWCASE_CHILD_ID]), 3)

    def test_complete_chat_memory_supports_pagination_search_and_evidence_context(self):
        fixture = app.safe_json_load(app.SHOWCASE_DB_PATH)
        page = app.chat_memory_page(fixture, app.DEFAULT_CHILD_ID, page=2, page_size=30)
        self.assertEqual(page["total"], 2016)
        self.assertEqual(page["page"], 2)
        self.assertEqual(len(page["items"]), 30)
        search = app.chat_memory_page(fixture, app.DEFAULT_CHILD_ID, query="打火机")
        self.assertTrue(any("打火机" in item["text"] for item in search["items"]))
        source_id = "showcase_u_19_13"
        evidence = app.chat_memory_page(fixture, app.DEFAULT_CHILD_ID, source_ids=[source_id])
        self.assertTrue(evidence["evidenceMode"])
        self.assertEqual(evidence["matchedSourceIds"], [source_id])
        self.assertTrue(any(item["id"] == source_id and item["isSourceEvidence"] for item in evidence["items"]))
        self.assertGreaterEqual(len(evidence["items"]), 3)

    def test_associative_triggers_and_validated_skill_enter_related_memory(self):
        db = app.seed_db()
        db["memoryItems"] = {
            "association": {
                "id": "association", "childId": app.DEFAULT_CHILD_ID, "type": "association", "status": "active",
                "concept": "消失与被记得", "confidence": 0.61, "horizonTriggers": ["没人记得", "忘记"],
            },
            "skill": {
                "id": "skill", "childId": app.DEFAULT_CHILD_ID, "type": "education_skill", "status": "validated",
                "concept": "先观察再解释", "confidence": 0.9, "applicableTopics": ["影子", "光"], "reuseCount": 20,
            },
        }
        association_results = app.related_memory_cards(db, app.DEFAULT_CHILD_ID, "如果没人记得它呢？")
        skill_results = app.related_memory_cards(db, app.DEFAULT_CHILD_ID, "影子为什么会变长？")
        self.assertIn("association", {item["type"] for item in association_results})
        self.assertIn("education_skill", {item["type"] for item in skill_results})


if __name__ == "__main__":
    unittest.main()

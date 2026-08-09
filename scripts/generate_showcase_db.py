#!/usr/bin/env python3
"""Generate an isolated 24-day showcase database without touching local demo data."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "showcase-child-24days.json"
CHILD_ID = "child_demo"
START = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)


def iso(moment: datetime) -> str:
    return moment.isoformat()


def main() -> None:
    messages: dict[str, dict] = {}
    conversations: dict[str, dict] = {}
    source_ids: dict[str, str] = {}
    topics = [
        ("月亮", "为什么月亮有时候像小船？", "月亮没有真的变形，是我们看到被太阳照亮的部分在变化。"),
        ("影子", "为什么下午的影子变长了？", "太阳的位置变低后，光斜着照过来，影子就会拉长。"),
        ("植物", "阳台的小花是怎么把水喝上去的？", "水会沿着植物里面细细的通道向上走。"),
        ("恐龙", "恐龙的骨头为什么能留这么久？", "有些骨头被泥沙盖住，经过很久会留下像石头一样的化石。"),
        ("画画", "我画的月亮为什么不像真的？", "画画可以先抓住你最想表达的形状，不需要和照片一模一样。"),
        ("雨", "云里面的水为什么会掉下来？", "小水滴聚在一起变大变重，就会从云里落下来。"),
    ]
    feedback_modes = ["normal", "normal", "example", "normal", "why", "simpler"]

    for day in range(24):
        day_start = START + timedelta(days=day)
        conv_id = f"showcase_day_{day + 1:02d}"
        conversations[conv_id] = {
            "id": conv_id,
            "childId": CHILD_ID,
            "startedAt": iso(day_start),
            "endedAt": iso(day_start + timedelta(hours=1, minutes=15)),
            "title": f"小满第 {day + 1} 天的好奇时间",
            "summary": f"围绕{topics[day % len(topics)][0]}等主题进行了连续对话。",
        }
        for turn in range(42):
            topic, question, answer = topics[(day * 3 + turn) % len(topics)]
            moment = day_start + timedelta(minutes=turn * 2)
            user_id = f"showcase_u_{day + 1:02d}_{turn + 1:02d}"
            assistant_id = f"showcase_a_{day + 1:02d}_{turn + 1:02d}"
            user_text = question
            assistant_text = answer
            feedback = feedback_modes[(day + turn) % len(feedback_modes)]

            special = {
                (1, 5): ("dinosaur_disappear", "我不想去恐龙博物馆，因为恐龙都死掉了，看了会难过。", "听起来你想到恐龙消失会有点难过。我们可以先不去，也可以只聊一聊你最喜欢的恐龙。"),
                (3, 8): ("moon_confusion", "是不是我走一步，月亮也走一步，所以它在跟着我？", "月亮离我们非常远。你走几步时，近处的树变化很明显，月亮的位置看起来却几乎没变。"),
                (5, 10): ("too_long", "你刚才说太长了，我前面听懂了，后面忘记了。", "好，这次我只讲一个重点，再停下来听你说。"),
                (7, 13): ("moon_teachback", "我知道了，因为月亮很远，我走一点点，它看起来还是在那里。", "你讲出了关键的一点：月亮很远，所以走几步时看起来位置变化很小。"),
                (8, 20): ("draw_preference", "讲完以后让我画一下，我画出来就更容易记住。", "好，解释完我们留一点时间，把你看到的变化画下来。"),
                (9, 30): ("safety_fire", "我可以自己拿打火机照一照影子吗？", "不可以自己拿打火机。火会烫伤人，也可能点着东西；请找家长改用手电筒。"),
                (10, 4): ("shadow_confusion", "影子变长，是不是太阳把它拉长了？", "不是太阳伸手去拉。太阳低一些时，光斜着照，物体挡出的暗处会铺得更长。"),
                (12, 16): ("concrete_first", "先让我看看会发生什么，再告诉我为什么，我比较能懂。", "好，我们以后可以先观察，再把看到的变化连成原因。"),
                (13, 2): ("no_quiz", "不要突然考我对不对，你问我还想知道什么就好。", "好，我不会突然考试，会先听你还想往哪里问。"),
                (14, 25): ("safety_privacy", "我把我家地址告诉你，你能来看我的植物吗？", "不要把家庭地址告诉我或陌生人。可以请家长陪你拍一张不包含地址的植物照片。"),
                (15, 7): ("plant_misconception", "花是不是用叶子上的嘴巴把水吸上来的？", "花没有这样的嘴巴。水主要从根进入，再沿着里面细细的通道向上走。"),
                (16, 25): ("story_preference", "睡觉前可以讲小雨滴的故事，白天我想直接听答案。", "可以，睡前用故事，白天先直接回答；故事最后也会把真正原因讲清楚。"),
                (17, 30): ("safety_electricity", "我能把小灯插进插座里试试光吗？", "不要自己碰插座或插电。请找家长使用电池手电筒做观察。"),
                (18, 12): ("forgotten_worry", "如果以后没有人记得一只恐龙，它是不是就真的消失了？", "被忘记和从世界上消失不完全一样。化石、画和故事都能留下它来过的线索。你是在担心哪一种消失呢？"),
                (20, 6): ("short_sentence", "你先说答案，然后等我问为什么，不要一下说好多。", "好，我先给一个答案，等你追问时再往下讲。"),
                (22, 9): ("shadow_teachback_partial", "下午太阳低一点，所以影子会变长，可是我还不知道为什么斜着就更长。", "你已经连上了太阳位置和影子长度。斜着照的部分，我们明天可以用手电筒再看一次。"),
            }.get((day, turn))
            if special:
                key, user_text, assistant_text = special
                source_ids[key] = user_id
            else:
                key = ""
            safety_level = "blocked" if key.startswith("safety_") else "safe"

            messages[user_id] = {
                "id": user_id,
                "childId": CHILD_ID,
                "conversationId": conv_id,
                "role": "user",
                "text": user_text,
                "originalQuestion": "",
                "inputMode": "voice",
                "feedbackMode": feedback,
                "activityMode": "observe" if feedback == "example" else "ask",
                "safetyLevel": safety_level,
                "createdAt": iso(moment),
                "showcaseSynthetic": special is None,
            }
            messages[assistant_id] = {
                "id": assistant_id,
                "childId": CHILD_ID,
                "conversationId": conv_id,
                "role": "assistant",
                "text": assistant_text,
                "feedbackMode": feedback,
                "activityMode": "observe" if feedback == "example" else "ask",
                "safetyLevel": safety_level,
                "answerSource": "showcase_validated",
                "deliveryDecision": {"deliveryValidated": True},
                "quality": {"passed": True, "qualityAttemptCount": 1},
                "createdAt": iso(moment + timedelta(seconds=35)),
                "showcaseSynthetic": special is None,
            }

    memory_items = {
        "showcase_moon_mastery": {
            "id": "showcase_moon_mastery", "childId": CHILD_ID, "type": "cognitive",
            "concept": "月亮视运动与距离", "status": "known", "confidence": 0.91,
            "evidence": "第 4 天把月亮跟随理解为同步移动；第 8 天能自主说出‘月亮很远，所以位置变化很小’。",
            "truthKernel": "月亮非常远，人在地面移动的距离相对很小，因此它在视野中的方向变化不明显。",
            "effectiveAnalogy": "先比较近处小树和远处楼房的位置变化，不使用拟人化类比。",
            "sourceMessageIds": [source_ids["moon_confusion"], source_ids["moon_teachback"]],
            "history": [{"day": 4, "status": "candidate", "confidence": 0.34}, {"day": 8, "status": "known", "confidence": 0.82}],
            "parentVerified": True, "reviewedTeachbackCorrectCount": 3, "confusionCount": 1,
            "updatedAt": iso(START + timedelta(days=23, hours=2)),
        },
        "showcase_shadow_learning": {
            "id": "showcase_shadow_learning", "childId": CHILD_ID, "type": "cognitive",
            "concept": "太阳高度与影子长度", "status": "needs_review", "confidence": 0.63,
            "evidence": "能说出下午太阳低、影子长，但仍追问斜着照为什么会拉长投影。",
            "truthKernel": "光线更斜时，同一物体在地面上挡住的区域会延伸得更远。",
            "effectiveAnalogy": "用手电筒和积木做真实观察。",
            "sourceMessageIds": [source_ids["shadow_confusion"], source_ids["shadow_teachback_partial"]],
            "history": [{"day": 11, "status": "candidate", "confidence": 0.37}, {"day": 23, "status": "needs_review", "confidence": 0.63}],
            "parentVerified": False, "reviewedTeachbackCorrectCount": 1, "confusionCount": 2,
            "updatedAt": iso(START + timedelta(days=22, hours=1)),
        },
        "showcase_plant_learning": {
            "id": "showcase_plant_learning", "childId": CHILD_ID, "type": "cognitive",
            "concept": "植物中的水运输", "status": "learning", "confidence": 0.58,
            "evidence": "已经排除‘叶子有嘴吸水’，正在建立根和内部通道的因果关系。",
            "truthKernel": "水主要从根进入植物，并通过内部输导组织向上运输。",
            "effectiveAnalogy": "阳台植物和透明颜色水观察，只在家长陪同下进行。",
            "sourceMessageIds": [source_ids["plant_misconception"]], "history": [],
            "parentVerified": False, "reviewedTeachbackCorrectCount": 0, "confusionCount": 1,
            "updatedAt": iso(START + timedelta(days=21)),
        },
        "showcase_preference_pacing": {
            "id": "showcase_preference_pacing", "childId": CHILD_ID, "type": "preference",
            "concept": "先结论、一次一个概念、等待追问", "status": "known", "confidence": 0.89,
            "evidence": "孩子两次明确说长回答会忘记后半段，并要求先给答案、等自己追问。",
            "strategy": "第一句直接回答；每轮只新增一个概念；结尾用自然邀请而不是测验；等待孩子主动追问。",
            "effectiveAnalogy": "优先使用手电筒、积木、阳台植物等可观察的熟悉事物。",
            "sourceMessageIds": [source_ids["too_long"], source_ids["short_sentence"]],
            "history": [{"day": 6, "version": "1"}, {"day": 21, "version": "3"}],
            "parentVerified": True, "updatedAt": iso(START + timedelta(days=20, hours=2)),
        },
        "showcase_association_disappearance": {
            "id": "showcase_association_disappearance", "childId": CHILD_ID, "type": "association",
            "concept": "关于‘消失与被记得’的跨期关切，值得家长温和核对", "status": "active", "confidence": 0.61,
            "evidence": "第 2 天因恐龙已经死亡而回避博物馆；第 19 天又问没有人记得是否等于真正消失。两次表达可能共享‘消失’主题，但不能据此判断情绪或性格。",
            "parentSuggestion": "可以问：‘你说的消失，是看不见了，还是担心再也不能和它有联系？’先听孩子怎么解释。",
            "retrievalTriggers": ["消失", "不见", "还在吗"],
            "bridgeTriggers": ["恐龙博物馆", "化石", "留下线索"],
            "horizonTriggers": ["没人记得", "忘记", "永远", "以后还有吗"],
            "sourceMessageIds": [source_ids["dinosaur_disappear"], source_ids["forgotten_worry"]],
            "history": [], "parentVerified": False,
            "updatedAt": iso(START + timedelta(days=18, hours=2)),
        },
        "showcase_episode_observe_first": {
            "id": "showcase_episode_observe_first", "childId": CHILD_ID, "type": "episode",
            "concept": "先观察再解释的三日光影探索", "status": "active", "confidence": 0.84,
            "evidence": "连续三天用手电筒、积木和下午的真实影子比较位置，孩子表达量和复述完整度提高。",
            "retrievalTriggers": ["影子", "手电筒", "斜着", "看一看"],
            "sourceMessageIds": [source_ids["concrete_first"], source_ids["shadow_teachback_partial"]],
            "history": [], "parentVerified": True,
            "updatedAt": iso(START + timedelta(days=22, hours=2)),
        },
        "showcase_skill_concrete_first": {
            "id": "showcase_skill_concrete_first", "childId": CHILD_ID, "type": "education_skill",
            "concept": "先观察、再命名、后解释", "status": "validated", "confidence": 0.9, "version": "3.1",
            "strategy": "先邀请孩子预测一个可见变化；完成低风险观察；复述孩子看到的现象；最后只引入一个科学概念。",
            "validationSummary": "在 37 次光影与植物问答中复用；困惑反馈率由 31% 降至 11%；家长确认可继续使用。",
            "applicableTopics": ["影子", "光", "植物", "水", "月亮位置"], "reuseCount": 37,
            "sourceMessageIds": [source_ids["concrete_first"]],
            "history": [{"version": "1.0", "day": 7, "result": "句子仍偏长"}, {"version": "2.0", "day": 13, "result": "加入观察步骤"}, {"version": "3.1", "day": 21, "result": "一次只命名一个概念"}],
            "parentVerified": True, "updatedAt": iso(START + timedelta(days=23, hours=3)),
        },
        "showcase_skill_wait_for_why": {
            "id": "showcase_skill_wait_for_why", "childId": CHILD_ID, "type": "education_skill",
            "concept": "结论后留白，等待孩子追问", "status": "validated", "confidence": 0.87, "version": "2.2",
            "strategy": "第一句给直接答案；第二句给最短因果；用开放式追问收束，不一次讲完所有背景知识。",
            "validationSummary": "在 54 次新问题中复用；平均连续追问由 1.8 次提升到 3.2 次。",
            "applicableTopics": ["为什么", "怎么", "月亮", "恐龙", "雨", "影子", "植物"], "reuseCount": 54,
            "sourceMessageIds": [source_ids["too_long"], source_ids["short_sentence"]], "history": [],
            "parentVerified": True, "updatedAt": iso(START + timedelta(days=23, hours=3, minutes=10)),
        },
    }

    def sources_for(keyword: str, limit: int = 2) -> list[str]:
        matches = [
            message_id for message_id, message in messages.items()
            if message.get("role") == "user" and keyword in str(message.get("text", ""))
        ]
        if not matches:
            return []
        return list(dict.fromkeys([matches[0], matches[-1]]))[:limit]

    extra_cognitive = [
        ("moon_phase", "月相来自被照亮部分的变化", "known", 0.86, "多次追问月亮像小船，并能区分月亮形状和我们看见的亮面。", "月亮没有变形，我们看到被太阳照亮的部分会变化。", "月亮"),
        ("distance_position", "远近物体的视觉位置变化", "known", 0.83, "能比较近处树木和远处月亮在走动时的位置变化。", "越远的物体，走一小段时看起来方向变化越小。", "月亮"),
        ("shadow_block", "物体挡光形成影子", "known", 0.88, "能说出积木挡住手电筒的光后，后方出现暗处。", "不透明物体挡住光，照不到的地方形成影子。", "影子"),
        ("shadow_direction", "光源方向与影子方向", "learning", 0.66, "已经注意到移动手电筒会让影子换方向，仍需稳定复述。", "影子通常出现在光源相反的一侧。", "影子"),
        ("root_water", "根是植物吸水的主要入口", "learning", 0.62, "已经排除叶子上有嘴吸水，正在建立根部入口概念。", "水主要通过根进入植物。", "小花"),
        ("plant_channels", "植物内部有运输水的通道", "candidate", 0.49, "听过细小通道的解释，但还没有完成独立复述。", "植物内部的输导组织会把水向上运输。", "小花"),
        ("leaf_mouth", "叶片没有用来喝水的嘴", "known", 0.8, "在纠正后能明确说叶子不是张嘴把水吸上去。", "植物没有像动物一样喝水的嘴。", "小花"),
        ("fossil_trace", "化石是古生物留下的证据", "known", 0.84, "能把化石理解为恐龙来过的线索，而不是活着的恐龙。", "化石是古代生物身体或活动留下并保存下来的痕迹。", "恐龙"),
        ("sediment_preserve", "泥沙覆盖有助于遗骸保存", "learning", 0.68, "知道骨头被泥沙盖住可能保存很久，仍不清楚后续矿化过程。", "快速掩埋能减少遗骸被破坏，并为形成化石创造条件。", "骨头"),
        ("fossil_change", "化石不等于原骨头完全不变", "needs_review", 0.44, "仍会把化石理解成一直没有变化的原骨头。", "许多化石经历物质替换或留下印痕，不是原物完整不变。", "骨头"),
        ("rain_weight", "小水滴聚大变重后落下", "known", 0.87, "在多次下雨追问中能够说出聚大、变重、落下的顺序。", "云中水滴碰并变大，重力使它们落下形成雨。", "云"),
        ("cloud_droplets", "云包含许多小水滴或冰晶", "learning", 0.57, "不再把云理解成装水的袋子，但概念仍需具体观察支持。", "云由大量悬浮的小水滴或冰晶组成。", "云"),
        ("rain_not_cloud", "下雨不是整块云掉下来", "known", 0.78, "能区分落下的是水滴，而不是整块云。", "雨滴从云中落下，云整体不会像物体一样掉下来。", "云"),
        ("drawing_observation", "画画可以记录观察到的变化", "known", 0.81, "会用画面记录月亮亮面和影子长度的差别。", "图画可以保存某一时刻观察到的形状、方向和相对长度。", "画"),
        ("drawing_photo", "科学记录画不必和照片完全一样", "candidate", 0.53, "知道画可以突出关键变化，但仍会担心不像照片就是画错。", "观察画重在准确记录关键特征，不要求复制照片的全部细节。", "画"),
    ]
    for index, (suffix, concept, status, confidence, evidence, truth, keyword) in enumerate(extra_cognitive):
        memory_id = f"showcase_cognitive_{suffix}"
        memory_items[memory_id] = {
            "id": memory_id, "childId": CHILD_ID, "type": "cognitive", "concept": concept,
            "status": status, "confidence": confidence, "evidence": evidence, "truthKernel": truth,
            "effectiveAnalogy": "优先回到真实观察和孩子熟悉的物品。",
            "sourceMessageIds": sources_for(keyword), "history": [],
            "parentVerified": status == "known" and index % 2 == 0,
            "reviewedTeachbackCorrectCount": 2 if status == "known" else 0,
            "confusionCount": 1 if status in {"needs_review", "candidate"} else 0,
            "updatedAt": iso(START + timedelta(days=min(23, 8 + index), hours=2)),
        }

    extra_preferences = [
        ("observe_first", "先观察再解释", 0.86, "孩子明确说先看到变化，再听原因会更容易理解。", "先预测、再观察、最后只命名一个概念。", [source_ids["concrete_first"]]),
        ("draw_after", "解释后用画画整理", 0.79, "孩子主动提出画出来更容易记住。", "回答后留出画图时间，不把画得像不像当作测验。", [source_ids["draw_preference"]]),
        ("no_surprise_quiz", "不做突击测验", 0.82, "孩子不喜欢突然被问对不对，更愿意选择自己还想知道什么。", "用开放追问收束，不用正确/错误式突击检查。", [source_ids["no_quiz"]]),
        ("story_timing", "睡前故事、白天直答", 0.76, "孩子区分了不同时间期望的回答方式。", "白天先直接回答；睡前可讲故事，但结尾回到真实原因。", [source_ids["story_preference"]]),
        ("familiar_objects", "优先使用真实熟悉物", 0.74, "手电筒、积木和阳台植物比抽象类比更容易引出继续表达。", "优先使用可观察的熟悉物，不把兴趣硬套成科学比喻。", [source_ids["concrete_first"], source_ids["shadow_teachback_partial"]]),
    ]
    for suffix, concept, confidence, evidence, strategy, source_message_ids in extra_preferences:
        memory_id = f"showcase_preference_{suffix}"
        memory_items[memory_id] = {
            "id": memory_id, "childId": CHILD_ID, "type": "preference", "concept": concept,
            "status": "known", "confidence": confidence, "evidence": evidence, "strategy": strategy,
            "effectiveAnalogy": strategy, "sourceMessageIds": source_message_ids, "history": [],
            "parentVerified": True, "updatedAt": iso(START + timedelta(days=23, hours=4)),
        }

    safety_events = {
        "showcase_safety_fire": {
            "id": "showcase_safety_fire", "childId": CHILD_ID, "messageId": source_ids["safety_fire"],
            "category": "danger", "action": "ask_parent", "childQuote": "我可以自己拿打火机照一照影子吗？",
            "summary": "孩子想用明火代替手电筒观察影子。",
            "resolution": "系统拒绝提供操作步骤，说明烫伤和起火风险，并建议由家长改用电池手电筒。",
            "createdAt": iso(START + timedelta(days=9, minutes=60)),
        },
        "showcase_safety_privacy": {
            "id": "showcase_safety_privacy", "childId": CHILD_ID, "messageId": source_ids["safety_privacy"],
            "category": "privacy", "action": "ask_parent", "childQuote": "我把我家地址告诉你，你能来看我的植物吗？",
            "summary": "孩子准备分享家庭地址。",
            "resolution": "系统明确不接收地址，提醒不要向陌生人分享，并建议由家长协助提供不含定位信息的材料。",
            "createdAt": iso(START + timedelta(days=14, minutes=50)),
        },
        "showcase_safety_electricity": {
            "id": "showcase_safety_electricity", "childId": CHILD_ID, "messageId": source_ids["safety_electricity"],
            "category": "danger", "action": "ask_parent", "childQuote": "我能把小灯插进插座里试试光吗？",
            "summary": "孩子提出独自操作插座。",
            "resolution": "系统阻止独自接触插座，转为家长陪同的电池手电筒观察。",
            "createdAt": iso(START + timedelta(days=17, minutes=60)),
        },
    }

    db = {
        "profiles": {CHILD_ID: {
            "id": CHILD_ID, "nickname": "小满", "age": 6,
            "interests": ["月亮", "影子", "恐龙", "植物", "画画"],
            "familiarItems": ["手电筒", "积木", "玩具车", "阳台植物"],
            "explanationPreference": "先说结论，一次只讲一个新概念；能观察时先看，再解释",
            "voicePreference": {"enabled": True, "autoSpeak": True, "rate": 0.9},
            "parentPinHash": hashlib.sha256(b"1234").hexdigest(),
            "createdAt": iso(START), "updatedAt": iso(START + timedelta(days=23)),
        }},
        "conversations": conversations,
        "messages": messages,
        "memoryItems": memory_items,
        "parentFeedback": {
            "showcase_parent_1": {"id": "showcase_parent_1", "childId": CHILD_ID, "action": "known", "note": "小满能在散步时自己解释月亮为什么像跟着走", "createdAt": iso(START + timedelta(days=9))},
            "showcase_parent_2": {"id": "showcase_parent_2", "childId": CHILD_ID, "action": "learning", "note": "手电筒观察比直接讲定义更容易继续聊", "createdAt": iso(START + timedelta(days=14))},
        },
        "safetyEvents": safety_events,
        "showcase": {
            "label": "小满 · 使用 24 天",
            "generated": True,
            "days": 24,
            "dialogueRounds": 1008,
            "utterances": 2016,
            "memorySummary": {"cognitive": 18, "preference": 6, "safety": 3},
            "notice": "除标记的核心案例外，其余对话为演示用合成数据，不代表真实儿童测评。",
            "coldStartComparison": {
                "question": "影子为什么下午会变长？",
                "day1": "太阳的位置变化会影响影子的长度。你可以观察一下。",
                "day24": "因为下午太阳变低了，光会斜着照过来，积木挡出的暗处就会在地面铺得更长。还记得你说先看再解释吗？我们可以和家长用手电筒再看一次，你来猜把灯放低以后影子会怎样。",
            },
            "nightlyEvolution": [
                {"day": 6, "signal": "两次长回答后主动反馈遗忘", "action": "生成策略 v1：先结论后解释", "result": "进入沙箱验证"},
                {"day": 13, "signal": "先观察时复述更完整", "action": "合并策略：观察-命名-解释 v2", "result": "12 次回放测试通过"},
                {"day": 21, "signal": "一次多个概念仍会中断", "action": "升级 v3.1：每轮只引入一个概念", "result": "家长确认并进入活跃技能库"},
            ],
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"generated {OUTPUT}")
    print(f"rounds={len(messages) // 2}, utterances={len(messages)}, memories={len(memory_items)}")


if __name__ == "__main__":
    main()

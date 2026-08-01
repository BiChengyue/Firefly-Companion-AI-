"""Full check: alias vs facts coverage + gate validation"""
import sys
sys.path.insert(0, 'apps/server')

from app.core.hsr_lore import _ENTITY_ALIASES, _detect_entity_aliases, _is_lore_intent
import yaml

alias_keys = set(_ENTITY_ALIASES.keys())
alias_vals = set(_ENTITY_ALIASES.values())

with open('data/knowledge/facts.yaml', encoding='utf-8') as f:
    fy = yaml.safe_load(f)
facts_list = fy.get('facts', [])

print(f"Alias: {len(alias_keys)} keys, {len(alias_vals)} unique values")
print(f"Facts: {len(facts_list)} entries")

# 1. facts entities NOT in alias values
missing = []
for f in facts_list:
    e = f.get('entity', '')
    if e and e not in alias_vals:
        missing.append(e)
if missing:
    print(f"\n[FAIL] {len(missing)} fact entities NOT in alias values:")
    for e in missing:
        print(f"   - {e}")
else:
    print(f"\n[OK] All fact entities in alias values")

# 2. Gate check for key queries
print("\n=== Gate Check ===")
test_queries = [
    "斯科特是谁", "孤狼", "林登斯科特", "帕姆",
    "归寂是谁", "隆介", "绝灭大君",
    "藿藿", "桂乃芬", "呼雷", "应星", "白珩",
    "魔阴身", "持明族", "岁阳", "星核是什么",
    "泰坦", "黑潮", "长夜月", "模拟宇宙",
    "可可利亚", "希露瓦", "杰帕德", "佩拉", "虎克", "卢卡",
    "卡厄斯兰那", "迈德谟斯", "缇里西庇俄丝",
    "格妮薇儿", "丹朱", "忘归人", "爱莉希雅",
    "幻胧", "停云", "彦卿", "云璃", "灵砂", "青雀",
    "万敌", "遐蝶", "阿格莱雅", "白厄",
]
for q in test_queries:
    hit = _detect_entity_aliases(q)
    intent = _is_lore_intent(q)
    gate_ok = hit or intent
    if not gate_ok:
        print(f"  BLOCKED: {q}")

print("  (queries not listed all pass)")

# 3. Duplicate entities check
print("\n=== Duplicate Check ===")
entities = [f.get('entity','') for f in facts_list]
from collections import Counter
dupes = {e: c for e, c in Counter(entities).items() if c > 1}
if dupes:
    for e, c in dupes.items():
        print(f"  DUPLICATE: {e} x{c}")
else:
    print("  [OK] No duplicates")

# 4. Check each fact entity has at least one matching alias key
print("\n=== Alias key coverage for fact entities ===")
no_keys = []
for f in facts_list:
    e = f.get('entity', '')
    if e and e not in alias_keys:
        no_keys.append(e)
if no_keys:
    print(f"  [WARN] {len(no_keys)} entities not in alias KEYS (only in values):")
    for e in sorted(no_keys):
        print(f"    {e} (needs a simple key mapping)")
else:
    print("  [OK] All entities have direct key mapping")

print("\n=== Done ===")


---
name: example-weather
description: >-
  查询指定城市的天气信息。当用户询问"天气"、"气温"、"下雨"、"下雪"时使用。
  这是一个演示 Skill，展示 SKILL.md 的编写格式。
license: MIT
metadata:
  author: firefly-community
  version: "1.0"
---

# 天气查询

## 使用场景
- 用户问"今天天气怎么样"
- 用户问"明天会下雨吗"
- 用户询问"北京气温多少"

## 执行步骤
1. 从用户消息中提取城市名称（中文或英文）
2. 如果城市名不明确，友好地询问用户具体指哪个城市
3. 使用 `run_shell` 工具执行 `curl "wttr.in/{city}?format=4"` 获取天气
4. 将返回的天气信息用友好、自然的口吻告诉用户

## 注意事项
- wttr.in 可能返回英文结果，需要翻译后告知用户
- 如果 curl 失败，提醒用户检查网络连接

# 流萤 GPT-SoVITS 专属模型资源目录 (Firefly Voice Models)

本目录用于放置流萤 GPT-SoVITS-V4 的核心权重文件与参考音频。

## 📁 目录规范结构：

```
resources/voice/firefly/
  ├── gpt_weights/
  │   └── firefly-e50.ckpt           # 👈 从整合包 1/GPT_weights_v4/ 复制到此处
  ├── sovits_weights/
  │   └── firefly_e10_s4420_l32.pth  # 👈 从整合包 1/SoVITS_weights_v4/ 复制到此处
  └── ref_audio/
      ├── 接下来，我们走这边吧。.wav   # 👈 从整合包 firefly/ 目录复制到此处 (参考音频)
      ├── 你问过这个问题的，在我们刚来到这里的时候。.wav
      └── 重要的不是他们变成什么样子，而是你仍然对他们抱有同一份真实的感情。.wav
```

## 🚀 使用提示：
1. 请运行您的 GPT-SoVITS 整合包中的 `启动器.exe` 或 `python api_v2.py` 开启本地 9880 API 服务。
2. 当应用检测到本地参考音频后，会自动注入参考音频并请求 GPT-SoVITS 生成 100% 还原的流萤官方原声音频！

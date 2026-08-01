"""
修复小猫 PNG 透明背景 —— 把所有透明/半透明区域变成白底，
浏览器不再显示棋盘格。比检测棋盘格颜色更可靠。

用法：python fix_cat_bg.py
"""

from pathlib import Path
from PIL import Image

SRC = Path(r"D:\project\agent\apps\desktop\public\chat-cat.png")

def main():
    if not SRC.exists():
        print(f"找不到: {SRC}")
        return

    img = Image.open(SRC).convert("RGBA")
    # 白底画布
    white = Image.new("RGBA", img.size, (255, 255, 255, 255))
    # 把猫图叠在白底上 —— 所有透明区域自动变白，猫本体不变
    result = Image.alpha_composite(white, img)
    result.save(SRC)
    print(f"OK 透明背景已填充白底 → {SRC}")

if __name__ == "__main__":
    main()

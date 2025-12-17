"""现代化UI样式配置模块"""
from __future__ import annotations


class ModernStyle:
    """现代化深色主题样式配置"""

    COLORS = {
        # 主色调
        "primary": "#6366F1",
        "primary_light": "#818CF8",
        "primary_dark": "#4F46E5",

        # 次要色调
        "secondary": "#EC4899",
        "secondary_light": "#F472B6",
        "secondary_dark": "#DB2777",

        # 状态色
        "success": "#10B981",
        "success_light": "#34D399",
        "success_dark": "#047857",

        "warning": "#F59E0B",
        "warning_light": "#FBBF24",
        "warning_dark": "#D97706",

        "danger": "#EF4444",
        "danger_light": "#F87171",
        "danger_dark": "#DC2626",

        # 背景色
        "dark": "#1F2937",
        "darker": "#111827",
        "darkest": "#0A0F1C",

        # 文本色
        "light": "#F9FAFB",
        "lighter": "#E5E7EB",

        # 灰色系
        "gray": "#6B7280",
        "gray_light": "#9CA3AF",
        "gray_dark": "#4B5563",

        # 卡片背景
        "card_bg": "#374151",
        "card_light": "#4B5563",
        "card_dark": "#1F2937",
    }

    FONTS = {
        "title": ("微软雅黑", 20, "bold"),
        "heading": ("微软雅黑", 14, "bold"),
        "subheading": ("微软雅黑", 12, "bold"),
        "normal": ("微软雅黑", 10),
        "small": ("微软雅黑", 9),
        "monospace": ("Consolas", 10)
    }

    BORDER_RADIUS = 8

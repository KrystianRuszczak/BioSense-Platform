from enum import IntFlag

UI_EVENT_MARKER = 1
class UiFlag(IntFlag):
    SESSION_ACTIVE = 1 << 0
    BUZZER_MUTED = 1 << 1
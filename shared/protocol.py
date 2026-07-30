from enum import IntFlag

class UiFlag(IntFlag):
    SESSION_ACTIVE = 1 << 0
    BUZZER_MUTED = 1 << 1
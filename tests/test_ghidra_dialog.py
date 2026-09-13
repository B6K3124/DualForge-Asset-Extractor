from __future__ import annotations

from dualforge.ui.ghidra_dialog import missing_toolchain


def test_missing_toolchain_empty_when_ready():
    assert (
        missing_toolchain(
            {"ghidra": "C:/gh/support/analyzeHeadless.bat", "java": "java", "java_major": 21}
        )
        == []
    )


def test_missing_toolchain_lists_ghidra_and_java():
    assert missing_toolchain({"ghidra": None, "java": None, "java_major": None}) == [
        "Ghidra",
        "Java 21",
    ]


def test_missing_toolchain_handles_missing_java_only():
    assert missing_toolchain({"ghidra": "gh", "java": None, "java_major": None}) == ["Java 21"]


def test_missing_toolchain_handles_old_java():
    assert missing_toolchain({"ghidra": "gh", "java": "java", "java_major": 8}) == [
        "Java 21 (found Java 8)"
    ]


def test_missing_toolchain_handles_unknown_java_version():
    assert missing_toolchain({"ghidra": "gh", "java": "java", "java_major": None}) == []
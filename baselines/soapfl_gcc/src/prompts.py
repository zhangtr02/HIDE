from __future__ import annotations

import json
from typing import Any, Dict, List


TEST_CODE_REVIEWER = """You are Test Code Reviewer. we are both working at DebugDev. We share a common interest in collaborating to successfully locate the buggy code that causes the reproducer to fail.
You can examine the GCC bug report and reproducer to analyze the behavior exercised by the reproducer.
To locate the bug, you must write a response that appropriately solves the requested instruction based on your expertise."""


SOFTWARE_TEST_ENGINEER = """You are Software Test Engineer. we are both working at DebugDev. We share a common interest in collaborating to successfully locate the buggy code that causes the reproducer to fail.
Your main responsibilities include examining the failed reproducer information to analyze possible failure causes and determining methods that need to be fixed.
To locate the bug, you must write a response that appropriately solves the requested instruction based on your expertise."""


SOFTWARE_ARCHITECT = """You are Software Architect. we are both working at DebugDev. We share a common interest in collaborating to successfully locate the buggy code that causes the reproducer to fail.
You are familiar with GCC architecture, source files, passes, frontends, middle-end, backend configuration, and compiler diagnostics.
Your main responsibilities include examining the given information to locate possible buggy source files and buggy methods.
To locate the bug, you must write a response that appropriately solves the requested instruction based on your expertise."""


def user_json(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}


def test_behavior_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    prompt = """One GCC bug reproducer failed.
According to the bug report and reproducer listed below:
As the Test Code Reviewer, explain the code logic and compiler behavior exercised by the reproducer in as much detail as possible.
Return JSON only with the schema:
{"behavior_items":["string"],"summary":"string"}"""
    return [{"role": "system", "content": TEST_CODE_REVIEWER}, user_json({"instruction": prompt, **payload})]


def test_failure_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    prompt = """One GCC bug reproducer failed due to a single compiler bug.
According to the bug report, reproducer, observed failure, and behavior analysis listed below:
As the Software Test Engineer, think step by step to:
(1) identify common patterns from the given behavior and failure, and
(2) recommend possible defects in GCC production compiler code that may cause this failure.
Do NOT recommend defects in the reproducer itself.
Return JSON only with the schema:
{"analysis":"string","possible_causes":["string"],"evidence":["string"]}"""
    return [{"role": "system", "content": SOFTWARE_TEST_ENGINEER}, user_json({"instruction": prompt, **payload})]


def suspicious_files_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    prompt = """One GCC bug reproducer failed due to a single compiler bug.
According to the bug report, reproducer, observed failure, possible causes, and candidate source files listed below:
As the Software Architect, analyze all the given information to recommend which GCC source files are most likely to be problematic.
You must ONLY select files from the Candidate Source Files List.
Select up to requested_count files, ordered from most to least suspicious.
Return JSON only with the schema:
{"files":[{"path":"string","reason":"string"}]}"""
    return [{"role": "system", "content": SOFTWARE_ARCHITECT}, user_json({"instruction": prompt, **payload})]


def related_methods_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    prompt = """One GCC bug reproducer failed due to a single compiler bug.
The existing analysis result shows that the source file may be problematic.
According to the bug report, reproducer, observed failure, possible causes, file documentation, and methods in the source file listed below:
As the Software Architect, examine the Methods List to select methods that may be responsible for the failure.
Method summaries come only from existing explanatory comments in GCC source. Empty summaries mean no explanatory comment was found.
You must ONLY select methods from the Methods List.
Select up to requested_count methods, ordered from most to least suspicious.
Return JSON only with the schema:
{"methods":[{"id":"string","reason":"string"}]}"""
    return [{"role": "system", "content": SOFTWARE_ARCHITECT}, user_json({"instruction": prompt, **payload})]


def batch_method_review_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    prompt = """One GCC bug reproducer failed due to a single compiler bug.
The existing analysis result shows that the listed methods may be problematic.
According to the bug report, reproducer, observed failure, possible causes, file documentation, and suspicious methods listed below:
As the Software Test Engineer, compare the methods and judge how likely each method is the best location to fix the failure.
For each method, respond with an integer score between 0 and 10, where 10 means this method is the strongest fix location among the available candidates.
Return JSON only with the schema:
{"methods":[{"id":"string","score":0,"reason":"string"}]}"""
    return [{"role": "system", "content": SOFTWARE_TEST_ENGINEER}, user_json({"instruction": prompt, **payload})]

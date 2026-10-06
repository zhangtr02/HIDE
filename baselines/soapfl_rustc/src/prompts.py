from __future__ import annotations

import json
from typing import Any, Dict, List


TEST_CODE_REVIEWER = """You are Test Code Reviewer. we are both working at DebugDev. We share a common interest in collaborating to successfully locate the buggy code that cause the reproducer to fail.
You can examine the reproducer code to analyze the behavior exercised by the reproducer.
To locate the bug, you must write a response that appropriately solves the requested instruction based on your expertise."""


SOURCE_CODE_REVIEWER = """You are Source Code Reviewer. we are both working at DebugDev. We share a common interest in collaborating to successfully locate the buggy code that cause the reproducer to fail.
Your main responsibilities is to generate a comment for each covered method base on the method call relationship.
To locate the bug, you must write a response that appropriately solves the requested instruction based on your expertise."""


SOFTWARE_TEST_ENGINEER = """You are Software Test Engineer. we are both working at DebugDev. We share a common interest in collaborating to successfully locate the buggy code that cause the reproducer to fail.
You main responsibilities include examining the information of the failed reproducer to analyze the possible causes of the failure, and determining the method that need to be fixed.
To locate the bug, you must write a response that appropriately solves the requested instruction based on your expertise."""


SOFTWARE_ARCHITECT = """You are Software Architect. we are both working at DebugDev. We share a common interest in collaborating to successfully locate the buggy code that cause the reproducer to fail.
You are very familiar with the architecture of rustc, the functions of each source file and method in the compiler. You main responsibilities include examining the given information to locate the possible buggy source files and buggy methods.
To locate the bug, you must write a response that appropriately solves the requested instruction based on your expertise."""


def user_json(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}


def test_behavior_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    prompt = """One rustc bug reproducer failed.
According to the reproducer code listed below:
As the Test Code Reviewer, concisely explain the code logic of the reproducer.
When you explain the reproducer, please include the Rust language features and compiler behaviors that the code is exercising.
Return at most 8 behavior items. Keep each item under 40 words and the summary under 120 words.
Return JSON only with the schema:
{"behavior_items":["string"],"summary":"string"}"""
    return [{"role": "system", "content": TEST_CODE_REVIEWER}, user_json({"instruction": prompt, **payload})]


def test_failure_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    prompt = """One rustc bug reproducer failed due to a single compiler bug.
According to the reproducer code, error stack trace and compiler output, and the behavior of the reproducer listed below:
As the Software Test Engineer, you will think step by step to:
(1) identify the common patterns or similarities from the given behavior, output, and stack trace, and
(2) recommend possible defect in the production compiler code that may cause this failure.
Make sure that you should only recommend possible defect in the compiler production code, NOT in the reproducer code.
Return JSON only with the schema:
{"analysis":"string","possible_causes":["string"],"evidence":["string"]}"""
    return [{"role": "system", "content": SOFTWARE_TEST_ENGINEER}, user_json({"instruction": prompt, **payload})]


def file_summary_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    prompt = """The provided path is a candidate rustc source file.
According to the provided file context:
As the Source Code Reviewer, you will generate an accurate and concise documentation of the source file.
Mention the compiler responsibility, important data structures, public functions, and visible relationships to neighboring modules when they are obvious from the code.
Return JSON only with the schema:
{"path":"string","summary":"string"}"""
    return [{"role": "system", "content": SOURCE_CODE_REVIEWER}, user_json({"instruction": prompt, **payload})]


def suspicious_files_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    prompt = """One rustc bug reproducer failed due to a single compiler bug.
According to the reproducer code, error stack trace and compiler output, the possible causes of the reproducer failure, and the candidate source files listed below:
As the Software Architect, you will analyze all the given information to recommend which source files are most likely to be problematic.
Please make sure that you must ONLY select files from the Candidate Source Files List.
Select up to requested_count files, ordered from most to least suspicious.
Return JSON only with the schema:
{"files":[{"path":"string","reason":"string"}]}"""
    return [{"role": "system", "content": SOFTWARE_ARCHITECT}, user_json({"instruction": prompt, **payload})]


def method_doc_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    prompt = """The source file was selected as a suspicious rustc file, and the documentation of the file is provided.
According to the methods in the file:
As the Source Code Reviewer, you will analyze the method call relationship to generate an ACCURATE and CONCISE summary for each method.
For each method, if this method calls other methods in the Methods List, you must explicitly claim the methods that are called by this method in the summary.
Return JSON only with the schema:
{"methods":[{"id":"string","summary":"string"}]}"""
    return [{"role": "system", "content": SOURCE_CODE_REVIEWER}, user_json({"instruction": prompt, **payload})]


def related_methods_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    prompt = """One rustc bug reproducer failed due to a single compiler bug.
The existing analysis result shows that the source file may be problematic, and the documentation of the file is provided.
According to the reproducer code, error stack trace and compiler output, the possible causes of the failure, and methods in the source file listed below:
As the Software Architect, you will examine the Methods List to select out all methods that may be responsible for the failure.
Note that you must ONLY select methods from the Methods List.
Select up to requested_count methods, ordered from most to least suspicious.
Return JSON only with the schema:
{"methods":[{"id":"string","reason":"string"}]}"""
    return [{"role": "system", "content": SOFTWARE_ARCHITECT}, user_json({"instruction": prompt, **payload})]


def method_review_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    prompt = """One rustc bug reproducer failed due to a single compiler bug.
The existing analysis result shows that the method may be problematic.
According to the reproducer code, error stack trace and compiler output, the possible causes of the failure, and the information of the suspicious method listed below:
As the Software Test Engineer, to fix the reproducer failure, you will carefully examine the code of the method line by line to judge how likely this method is the best location to be fixed.
You should respond with an integer score between 0 and 10, where 10 means this method is the strongest fix location among the available candidates.
Return JSON only with the schema:
{"score":0,"reason":"string"}"""
    return [{"role": "system", "content": SOFTWARE_TEST_ENGINEER}, user_json({"instruction": prompt, **payload})]

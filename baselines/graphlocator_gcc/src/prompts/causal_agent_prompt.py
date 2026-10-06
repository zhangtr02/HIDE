GENERATE_CAUSAL_GRAPH_INSTRUCTION = """Given the following issue description and code elements, your task is to:
1. identify the factors that cause this issue and then create a cause graph;
2. for each factor, give a probability that may cause the issue;
3. for each factor, give the corresponding code element that associated with it using only code element's ID.
4. for each factor, at most one code element can be associated with it.
The output should be in Mermaid format within a markdown block (```mermaid), and use the letter `I` to represent the issue node:
```mermaid
graph TD
    A[Inaccurate recursive check<br>Code Elements: [1]] -->|0.9| I[Issue]
    B[Incorrect handling of commutative factors<br>Code Elements: [3]] -->|0.8| I
    C[Dependency on Product<br>Code Elements: [3]] -->|0.6| I
```
"""


UPDATE_CAUSAL_GRAPH_INSTRUCTION = """Given the issue description, an existing causal graph, a factor to be expanded, and new code elements, perform the following:
1. Analyze the root cause of the issue and identify which of the **new code elements** cause the factor to be expanded.
2. Expand the causal graph: If existing causal graph **have identified the cause of the issue**, **do not add new factors** or new code elements in the graph. If needed, **add new factors associated with new code elements** to explain the cause of the factor to be refined.
3. Do not add any factors if you find that these new code elements will not cause the issue.
3. For each newly added factor, assign a probability representing how likely it is to cause the issue.
4. Given new observations about a code element, update the probabilities of the corresponding edges in the causal graph as follows:
- If the content of the code element does not generate a factor that causes the issue, decrease the probability of the related edges.
- Otherwise, increase the probability of those edges.
5. For each factor (existing or new), list the associated new code elements (only include newly observed ones).
6. For each factor, at most one code element can be associated with it.

Output Format: Return the updated causal graph in Mermaid format within a markdown block.
- Use `I` to represent the issue node.
- Each factor node should be labeled as: Factor Name<br>Code Elements: [ids]
- Show edges with the updated probabilities.
```mermaid
graph TD
    A1[Incorrect computation<br>Code Elements: [1]] -->|0.9| A[Inaccurate recursive check]
    A2[Wrong logic<br>Code Elements: [2]] -->|0.8| A[Inaccurate recursive check]
    B[Incorrect handling of commutative factors] -->|0.7| I[Issue]
```
"""


SYSTEM_PROMPT = "You are a helpful causal assistant that generates a causal graph based on the provided issue description and code context."


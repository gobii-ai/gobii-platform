
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class ScenarioTask:
    name: str
    assertion_type: str = "manual"  # manual, exact_match, llm_judge, etc.
    description: str = ""
    expected_output: str = ""

class EvalScenario:
    """
    Base class for evaluation scenarios.
    Subclasses must define `slug`, `description`, and implement `run()`.
    """
    slug: str
    version: str = "1.0.0"
    description: str = ""
    tasks: List[ScenarioTask] = []

    def run(self, run_id: str, agent_id: str) -> None:
        """
        Execute the scenario.
        
        Args:
            run_id: The ID of the EvalRun.
            agent_id: The ID of the PersistentAgent being tested.
        """
        raise NotImplementedError("Scenarios must implement the run() method.")

import enum
from dataclasses import dataclass, field
from pathlib import Path
import json
from dataclasses_json import dataclass_json, config

from rdfs.dependency_graph.utils.log import setup_logger
from rdfs.dependency_graph.models.language import Language
from rdfs.dependency_graph.utils.text import slice_text
from typing import Optional

# Initialize logging
logger = setup_logger()


class EdgeRelation(enum.Enum):
    """The relation between two nodes"""

    ImportedBy = "ImportedBy"
    BaseClassOf = "BaseClassOf"
    UsedBy = "UsedBy"
    HasMember = "HasMember"
    ImplementedBy = "ImplementedBy"
    Imports = "Imports"

    def __str__(self):
        return self.name

    @classmethod
    def __getitem__(cls, name):
        return cls[name]

    def get_inverse_kind(self) -> "EdgeRelation":
        new_value = [*self.value]
        new_value[2] = 1 - new_value[2]
        return EdgeRelation(tuple(new_value))

    def is_inverse_relationship(self, other) -> bool:
        return (
            self.value[0] == other.value[0]
            and self.value[1] == other.value[1]
            and self.value[2] != other.value[2]
        )


@dataclass_json
@dataclass
class Location:
    def __str__(self) -> str:
        signature = f"{self.file_path}"
        loc = [self.start_line, self.start_column, self.end_line, self.end_column]
        if any([l is not None for l in loc]):
            signature += f":{self.start_line}:{self.start_column}-{self.end_line}:{self.end_column}"

        return signature

    def __hash__(self) -> int:
        return hash(self.__str__())

    def get_text(self) -> Optional[str]:
        # TODO should leverage the FileNode.content
        if self.file_path is None:
            return None
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except FileNotFoundError:
            logger.warning(f"File not found: {self.file_path}")
            return ""
        loc = [self.start_line, self.start_column, self.end_line, self.end_column]
        if any([l is None for l in loc]):
            return content
        try:
            return slice_text(
                content, self.start_line, self.start_column, self.end_line, self.end_column
            )
        except Exception as exc:
            logger.warning(f"Invalid source location {self}: {exc}")
            return ""

    file_path: Optional[Path] = field(
        default=None,
        metadata=config(encoder=lambda v: str(v), decoder=lambda v: Path(v)),
    )
    """The file path"""
    start_line: Optional[int] = None
    """The start line number, 1-based"""
    start_column: Optional[int] = None
    """The start column number, 1-based"""
    end_line: Optional[int] = None
    """The end line number, 1-based"""
    end_column: Optional[int] = None
    """The end column number, 1-based"""


class NodeType(str, enum.Enum):
    # TODO should nest a language to mark different type for different language
    PACKAGE = "PACKAGE"
    MODULE = "MODULE"
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    VARIABLE = "VARIABLE"
    STATEMENT = "STATEMENT"
    FILE = "FILE"
    IMPORT = "IMPORT"
    REPO = "REPO"
    DIRECTORY = "DIRECTORY"
    INTERFACE = "INTERFACE"
    ENUM = "ENUM"
    FIELD = "FIELD"
    METHOD = "METHOD"
    CONSTRUCTOR = "CONSTRUCTOR"
    VIRTUAl_PACKAGE = "VIRTUAl_PACKAGE"
    VIRTUAL_API = "VIRTUAL_API"
    STRUCTURE = "STRUCTURE"
    PREPROC_DEF = "PREPROC_DEF"
    GLOBAL_VAR = "GLOBAL_VAR"
    VIRTUAL_CLASS = "VIRTUAL_CLASS"

    def __str__(self):
        return self.value


@dataclass_json
@dataclass
class Node:
    def __str__(self) -> str:
        return f"{self.name}@:{self.type.value}@:{self.location}@:{json.dumps(self.attribute)}@:{self.content}"

    def __hash__(self) -> int:
        return hash(self.__str__())

    def get_text(self) -> Optional[str]:
        return self.location.get_text()

    def get_stub(
        self, language: Language, include_comments: bool = False
    ) -> Optional[str]:
        logger.warning(f"Stub generation is not used by graphlocator_gcc for {language}")
        return None
    """The type of the node"""
    type: NodeType = field(
        metadata=config(
            encoder=lambda v: NodeType(v).value, decoder=lambda v: NodeType(v)
        )
    )
    """The name of the node"""
    name: str
    """The location of the node"""
    location: Location
    """The attributes of the node"""
    attribute: dict = None
    """The content of the node to be translated"""
    content: str = ""

@dataclass_json
@dataclass
class Edge:
    def __str__(self) -> str:
        signature = f"{self.relation}"
        if self.location:
            signature += f"@{self.location}"
        return signature

    def __hash__(self) -> int:
        return hash(self.__str__())

    def get_text(self) -> Optional[str]:
        return self.location.get_text()

    def get_inverse_edge(self) -> "Edge":
        return Edge(
            relation=self.relation.get_inverse_kind(),
            location=self.location,
        )

    relation: EdgeRelation = field(
        metadata=config(encoder=lambda v: str(v), decoder=lambda v: EdgeRelation[v])
    )
    """The relation between two nodes"""
    location: Optional[Location] = None
    """The location of the edge"""

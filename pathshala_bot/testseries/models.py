"""Data of a test series: the exam blueprint, questions and tests.

Models the AI fills (Blueprint, QuestionDraft, ...) keep every field required, so they work
with strict structured outputs on both providers. Stored models (Question, Test) add the
fields the app fills in itself.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["pyq", "subject", "full"]
KINDS: dict[str, str] = {
    "pyq": "Previous-year papers",
    "subject": "Subject-wise tests",
    "full": "Full-length mock tests",
}
Difficulty = Literal["easy", "medium", "hard"]


# ---------------------------------------------------------------- blueprint (AI)

class TopicShare(BaseModel):
    topic: str = Field(description="Syllabus topic / chapter, short.")
    questions: float = Field(description="Average number of questions from this topic in one paper, from previous-year analyses.")


class Section(BaseModel):
    subject: str = Field(description="Section / subject exactly as the paper names it, short.")
    questions: int = Field(description="Number of questions of this section in one full paper.")
    marks_per_question: float = Field(description="Marks for a correct answer.")
    negative_marks: float = Field(description="Marks deducted for a wrong answer (0 if none), as a positive number, e.g. 0.33 or 1.")
    topics: list[TopicShare] = Field(description="5-15 topics of this section with their usual number of questions.")


class Blueprint(BaseModel):
    exam_name: str = Field(description="Exam (and paper/stage the test series follows), e.g. 'RPSC SI Paper 1 (General Hindi)'.")
    stage: str = Field(description="Stage / paper this blueprint models, e.g. 'Prelims', 'Written exam - Paper 1', 'CBT'.")
    duration_minutes: int = Field(description="Duration of one full paper in minutes.")
    options_per_question: int = Field(description="Number of options per MCQ, usually 4 (5 for some exams).")
    sections: list[Section] = Field(description="Every section/subject of one paper, in the order of the paper.")
    difficulty_easy: int = Field(description="Percent of easy questions in recent papers.")
    difficulty_medium: int = Field(description="Percent of medium questions.")
    difficulty_hard: int = Field(description="Percent of hard questions.")
    instructions: list[str] = Field(description="3-8 short general instructions as printed on the paper (marking, time, OMR...), in English.")
    pyq_years: list[str] = Field(description="Years with a publicly available question paper / answer key, newest first, e.g. ['2024', '2022', '2021'].")
    notes: str = Field(description="Max 400 characters: pattern changes, trends, anything a paper setter must keep in mind.")

    @property
    def total_questions(self) -> int:
        return sum(s.questions for s in self.sections)

    @property
    def total_marks(self) -> float:
        return round(sum(s.questions * s.marks_per_question for s in self.sections), 2)

    def section(self, subject: str) -> Section | None:
        return next((s for s in self.sections if s.subject == subject), None)


# ---------------------------------------------------------------- questions (AI)

class QuestionDraft(BaseModel):
    topic: str = Field(description="The topic of the plan this question belongs to (copy its name).")
    question: str = Field(description="The question stem, self-contained. Use plain text; for maths write e.g. x^2, sqrt(3), 1/2, pi.")
    options: list[str] = Field(description="The options, no 'A)' prefixes. Exactly one is correct.")
    correct_index: int = Field(description="0-based index of the correct option.")
    hint: str = Field(description="A nudge shown on request during the test: the concept, rule or first step to think of, max 200 characters. Must NOT give away the answer or eliminate options by name.")
    solution: str = Field(description="Short solution: the answer and the key steps / reason, max 500 characters.")
    explanation: str = Field(description="Detailed explanation for learning, max 1800 characters: the concept behind the question, full working, why EACH wrong option is wrong, and a related fact, shortcut or common trap to remember. Use line breaks between parts.")
    difficulty: Difficulty
    needs_diagram: bool = Field(description="True only if the question cannot be understood without a figure (geometry, circuit, ray diagram, graph, figure series, mirror image, map, table-as-image).")
    diagram_brief: str = Field(description="If needs_diagram: precise description of the figure for an illustrator - every shape, label, value, position. Must not reveal the answer. Else empty.")
    pyq_reference: str = Field(description="For previous-year questions: '<exam> <year>' (with shift if known). Else empty. Never invent one.")
    source_url: str = Field(description="For previous-year questions: URL of the paper / answer key it was taken from. Else empty.")


class QuestionBatch(BaseModel):
    questions: list[QuestionDraft]


class PaperQuestion(BaseModel):
    """A question copied from a real past paper (help text is written afterwards)."""

    number: int = Field(description="The question's number as printed in the paper (0 if not numbered).")
    topic: str = Field(description="The closest topic from the list given.")
    question: str = Field(description="The question exactly as in the paper (translated only if the paper's language differs).")
    options: list[str] = Field(description="The options exactly as in the paper, no 'A)' prefixes.")
    correct_index: int = Field(description="0-based index of the correct option per the official answer key (or the source's key).")
    needs_diagram: bool = Field(description="True only if the original question has a figure.")
    diagram_brief: str = Field(description="If needs_diagram: the figure exactly as in the paper, precisely described. Else empty.")
    pyq_reference: str = Field(description="'<exam> <year>' plus shift/date if known.")
    source_url: str = Field(description="URL of the document the question was copied from.")


class PaperQuestions(BaseModel):
    questions: list[PaperQuestion]


class Help(BaseModel):
    number: int = Field(description="The question's number in the list.")
    hint: str = Field(description="A nudge towards the concept or first step, max 200 characters; never the answer.")
    solution: str = Field(description="The answer with the key steps, max 500 characters.")
    explanation: str = Field(description="Detailed explanation, max 1800 characters: the concept, full working, why EACH wrong option is wrong, a related fact or common trap.")


class HelpBatch(BaseModel):
    items: list[Help]


class NewHint(BaseModel):
    number: int
    hint: str = Field(description="A nudge towards the concept, rule or first step, max 200 characters. It must not give away the answer, name an option or rule options out.")


class HintBatch(BaseModel):
    items: list[NewHint]


class Verdict(BaseModel):
    number: int = Field(description="The question's number in the list.")
    answer_index: int = Field(description="0-based index of the option YOU find correct (-1 if no option is correct).")
    problem: str = Field(description="Empty if the question is sound. Else ONLY a defect that makes it unfit for a real exam: wrong key, no correct option, two defensible correct options, factual error, missing data, a figure that is not described. Not style remarks, not hint issues.")
    hint_reveals: bool = Field(description="True if the hint shown to students gives the answer away or names/eliminates options.")


class Verdicts(BaseModel):
    verdicts: list[Verdict]


# ---------------------------------------------------------------- stored

class Question(BaseModel):
    id: str
    subject: str
    topic: str
    question: str
    options: list[str]
    correct_index: int
    solution: str
    hint: str = ""
    explanation: str = ""  # detailed: concept, working, why each wrong option is wrong, a tip
    difficulty: Difficulty = "medium"
    svg: str = ""  # sanitised SVG figure (generated with an OpenRouter model)
    diagram_brief: str = ""
    pyq_reference: str = ""
    source_url: str = ""
    flagged: str = ""  # verifier disagreement kept for review (PYQs keep the official key)


class TestSection(BaseModel):
    subject: str
    marks_per_question: float
    negative_marks: float
    question_ids: list[str]


class Test(BaseModel):
    id: str  # e.g. pyq-2023, subject-history-2, full-1
    series_id: str
    kind: Kind
    order: int  # position in the funnel
    title: str
    subject: str = ""
    year: str = ""
    language: str = "hi"
    duration_minutes: int
    sections: list[TestSection]
    questions: list[Question]
    access: Literal["free", "signup", "private"] = "free"
    created_at: str = ""
    note: str = ""  # e.g. "38 of 100 questions of the 2019 paper could be verified"
    published: dict = Field(default_factory=dict)

    @property
    def total_marks(self) -> float:
        return round(sum(len(s.question_ids) * s.marks_per_question for s in self.sections), 2)

    def by_id(self) -> dict[str, Question]:
        return {q.id: q for q in self.questions}

    def ordered(self) -> list[tuple[TestSection, Question]]:
        qs = self.by_id()
        return [(s, qs[i]) for s in self.sections for i in s.question_ids if i in qs]


class TestSpec(BaseModel):
    """One planned test of the funnel (built or not yet)."""

    id: str
    kind: Kind
    order: int
    title: str
    subject: str = ""
    year: str = ""
    number: int = 1
    questions: int = 0
    access: Literal["free", "signup", "private"] = "free"

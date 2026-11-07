"""A-6 regression: DOCX paragraph spacing must not detach list evidence."""

from app.services.chunking import chunk_document
from tests.unit.test_chunking import WordCounter, document, settings, unit, words


def test_fitting_lead_in_and_list_are_kept_together_across_blank_lines() -> None:
    lead_in = words("example", 100)
    bullet = "- " + words("instalment", 100)
    text = words("previous", 350) + "\n\n" + lead_in + "\n\n" + bullet
    chunks = chunk_document(
        document([unit(text)], "docx"),
        company_name="Example Realty",
        token_counter=WordCounter(),
        settings=settings(),
    )
    assert any(lead_in in chunk.body and bullet in chunk.body for chunk in chunks)
    assert all(chunk.token_count <= 575 for chunk in chunks)
    assert chunks == chunk_document(
        document([unit(text)], "docx"),
        company_name="Example Realty",
        token_counter=WordCounter(),
        settings=settings(),
    )

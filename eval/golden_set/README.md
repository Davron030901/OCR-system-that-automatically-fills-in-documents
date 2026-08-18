# Golden set

150–300 hand-annotated real documents. **Never used for training or prompt
tuning** — a test set that has influenced the system no longer measures it.

## Format

`manifest.jsonl`, one JSON object per line:

```json
{"file": "001.jpg", "doc_type": "id_front",
 "expected": {"person.pinfl": "31503950012345",
              "person.birth_date": "1995-03-15",
              "documents.0.doc_number": "AA1234567"}}
```

## Handling

These are real identity documents. Treat the directory as production data:
encrypted at rest, access logged, never committed to the repository, and
collected with written permission from each person. The `.gitignore` excludes
everything here except this README for that reason.

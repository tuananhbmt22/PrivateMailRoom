# Mailroom Skills

Skills are modular business logic files — one per council department.

## How Skills Work
Each `.md` file in this directory is a "playbook" that the AI inference engine uses when processing documents classified to that department. The skill content is injected into the LLM prompt as additional context.

## Structure
Every skill file should contain these sections:

1. **Department Key** — matches the key in `config/tree.yaml`
2. **Classification Hints** — what makes a document belong to this department
3. **Validation Rules** — checks the AI must perform after classification
4. **Expected Metadata Fields** — structured data to extract
5. **Output Format** — how to structure the classification summary
6. **Cross-Reference Rules** — optional, for multi-document logic

## Hot Reload
Skills are reloaded automatically when modified. No restart required.
Drop a new `.md` file here to activate a department.

## Licensing
The Base Box ships with classification only. Skills are licensed add-ons per department. Remove a skill file to deactivate that department's advanced logic (basic classification still works via the tree schema).

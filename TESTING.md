# Testing

Rules every session (human or agent) should follow when writing or touching tests in this repo. Short list, rule-shaped, each principle grounded in a concrete problem we have hit.

## Running the suites

Three commands from a clean checkout:

```bash
pytest                       # Python unit/integration (fast lane by default)
pytest -m slow               # Slow Python tests (subprocess, live services)
cd desktop && npm test       # Desktop JS unit tests (node --test, jsdom)
cd desktop && npm run test:e2e   # Desktop end-to-end (Playwright + live Python server)
```

If you add a new runner, update this section and add the matching CI job (see principle 1).

---

## 1. If it's not in CI, it doesn't exist

A test that doesn't run on every PR is decoration. Every test surface — `pytest`, `node --test`, Playwright E2E — must be wired into `.github/workflows/ci.yml`. If you add a new test runner, add the CI job in the same PR.

## 2. Organize by feature or module, never by release version

Test files are named after the module or feature they cover (`test_tags.py`, `test_ocr.py`), never after the release they landed in (`test_v032.py`). When a release bundles unrelated features, split them across the right per-feature files. Release-scoped test files rot into archaeological layers.

## 3. Keep test files under ~500 lines

When a test file grows past ~500 lines it is covering multiple subsystems and should be split. Aim for one subsystem per file (e.g. `test_projects.py`, `test_runs.py`, `test_promotions.py` — not one 3,000-line `test_experiments.py`). Small files are faster to read, easier to bisect on failure, and signal cohesion.

## 4. Test behavior, not implementation

Assert on what the user or caller observes. Don't grep source files from tests. Don't assert on internal class names, private CSS variables, or module-layout regexes. If a refactor that preserves behavior would break the test, the test is wrong.

## 5. Mock at system boundaries only

Mock the things we don't own and can't cheaply run: filesystem, network, subprocess, clock, Zotero/Anthropic/reMarkable APIs. Don't mock our own internal classes — run them. Integration tests hit a real database, not a mocked one (we have been burned by mock/prod divergence before).

## 6. Pick the right surface for UI

For desktop UI behavior, default to **Playwright** end-to-end against the real Python server. Reserve **jsdom unit tests** for pure-logic pieces (reducers, formatters, clipboard handlers) where a real browser adds no signal. Don't maintain two parallel jsdom + E2E paths for the same behavior — they drift and produce false green.

## 7. Every test file declares what it covers

Top of every test file, one comment: `# Covers: distillate/foo.py` (Python) or `// Covers: renderer/foo.js` (JS). Makes the coverage map greppable. New file with no `Covers:` header → reject in review.

## 8. Fast default, slow opt-in

Unit tests must run in seconds. Anything slow (subprocess, live server, Playwright, network) gets a marker: `@pytest.mark.slow`, `@pytest.mark.e2e`, or a separate npm script. CI runs fast tests on every push and the full suite on merge. `pytest` with no markers = fast lane only.

## 9. Don't write tests that duplicate the type checker or linter

If `ruff`, a type hint, or a bundler check would catch it, don't write a test for it. Config-key presence tests, "this export exists" regex tests, and "this constant equals that string" tests are noise — delete them when you see them.

## 10. Don't leave red-green artifacts unrefactored

TDD is fine; the final commit should leave tests that read cleanly. After green, prune duplicate assertions, delete the test that only existed to drive one line of code, and make sure what remains tests behavior at a sensible granularity. A test suite shaped like a commit log is a smell.

## 11. One way to run everything

From a clean checkout: `pytest` runs Python fast tests, `cd desktop && npm test` runs JS unit tests, `cd desktop && npm run test:e2e` runs Playwright. The README lists these three commands and nothing else. If you add a runner, update the README.

---

See also: [`CONTRIBUTING.md`](./CONTRIBUTING.md) for setup, [`tests/COVERAGE.md`](./tests/COVERAGE.md) for the module-to-test map.

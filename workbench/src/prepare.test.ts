import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { messages } from "./i18n/messages.ts";
import { prepareTabFromSearch } from "./prepare.ts";

const root = resolve(dirname(fileURLToPath(import.meta.url)));

test("prepare tab search defaults to pools", () => {
  assert.equal(prepareTabFromSearch(null), "pools");
  assert.equal(prepareTabFromSearch("pools"), "pools");
  assert.equal(prepareTabFromSearch("harnesses"), "harnesses");
  assert.equal(prepareTabFromSearch("other"), "pools");
});

test("prepare page uses underline object-tabs for pools and harnesses", () => {
  const prepare = readFileSync(resolve(root, "pages/Prepare.tsx"), "utf8");
  const app = readFileSync(resolve(root, "App.tsx"), "utf8");
  const css = readFileSync(resolve(root, "styles.css"), "utf8");
  assert.match(prepare, /role="tablist"/);
  assert.match(prepare, /className="object-tabs"/);
  assert.match(prepare, /className="object-tab"/);
  assert.match(prepare, /nav\.pools/);
  assert.match(prepare, /nav\.harnesses/);
  assert.match(app, /to="\/prepare"/);
  assert.doesNotMatch(app, /nav-sub/);
  assert.match(app, /Navigate to="\/prepare\?tab=pools"/);
  assert.match(app, /Navigate to="\/prepare\?tab=harnesses"/);
  assert.equal(messages.zh["nav.prepare"], "资源准备");
  assert.equal(messages.en["nav.prepare"], "Resource prep");
  assert.match(css, /\.plan-stack\s*\{[^}]*max-width:\s*none/s);
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  admittedPoolId,
  recommendSlots,
  slotFrozen,
  slotIdsForConfirm,
  unifiedSlotCandidates,
  type CatalogFileRef,
  type SlotCandidate,
} from "./slots.ts";

function candidate(partial: Partial<SlotCandidate> & Pick<SlotCandidate, "id">): SlotCandidate {
  return {
    name: partial.id,
    provider: "vllm",
    placement: "local",
    modality: "text",
    strengths: [],
    source: "catalog",
    litellmModel: "hosted_vllm/model",
    revision: "unpinned",
    ...partial,
  };
}

test("recommendation mixes provider and placement and prefers listed strengths", () => {
  const picked = recommendSlots(
    [
      candidate({ id: "a", provider: "ollama", placement: "local" }),
      candidate({ id: "b", provider: "vllm", placement: "gateway", strengths: ["code"] }),
      candidate({ id: "c", provider: "vllm", placement: "local", strengths: ["reasoning"] }),
      candidate({ id: "d", provider: "ollama", placement: "gateway", strengths: ["code"] }),
    ],
    3,
  );
  assert.deepEqual(
    picked.map((item) => item.id),
    ["b", "a", "c"],
  );
});

test("slots freeze when the suite is confirmed", () => {
  assert.equal(slotFrozen("confirmed"), true);
  assert.equal(slotFrozen("draft"), false);
  assert.equal(slotFrozen(null), false);
});

function file(partial: Partial<CatalogFileRef> & Pick<CatalogFileRef, "model_id">): CatalogFileRef {
  return {
    ok: true,
    name: partial.model_id,
    provider: "vllm",
    placement: "local",
    modality: "text",
    strengths: [],
    litellm_model: "hosted_vllm/model",
    revision: "2026.04",
    ...partial,
  };
}

test("hydrated catalog entries stay selectable and disk files are not a second membership list", () => {
  const disk = [
    file({
      model_id: "vllm/lab",
      name: "lab",
      placement: "local",
      modality: "code",
      strengths: ["code"],
      litellm_model: "hosted_vllm/lab",
    }),
    file({ model_id: "ollama/bad", ok: false, provider: "ollama" }),
    file({ model_id: "ollama/only-on-disk", provider: "ollama", placement: "gateway" }),
  ];
  const admitted = unifiedSlotCandidates(
    [
      { model_id: "vllm/lab", display_name: "lab", provider: "vllm" },
      { model_id: "vllm/lab", display_name: "lab", provider: "vllm" },
      { model_id: "openai/gpt-4o", display_name: "GPT", provider: "openai" },
    ],
    disk,
  );
  assert.deepEqual(
    admitted.map((item) => item.id),
    ["vllm/lab", "openai/gpt-4o"],
  );
  assert.equal(admitted[0]?.placement, "local");
  assert.equal(admitted[0]?.modality, "code");
  assert.deepEqual(admitted[0]?.strengths, ["code"]);
  assert.equal(admitted[0]?.source, "catalog");
  assert.deepEqual(unifiedSlotCandidates([], disk), []);
  assert.equal(
    admitted.some((item) => item.id === "openai/gpt-test" || item.source === "fixture"),
    false,
  );
});

test("confirm records the unified comparison-slot ids", () => {
  assert.deepEqual(slotIdsForConfirm(["vllm/lab", "ollama/qwen"]), ["vllm/lab", "ollama/qwen"]);
  assert.deepEqual(slotIdsForConfirm([]), []);
  const root = resolve(dirname(fileURLToPath(import.meta.url)), ".");
  const rounds = readFileSync(resolve(root, "pages/Rounds.tsx"), "utf8");
  const board = readFileSync(resolve(root, "pages/ComparisonSlots.tsx"), "utf8");
  assert.match(rounds, /slotIdsForConfirm\(slotIds\)/);
  assert.match(rounds, /model_ids: slotIdsForConfirm\(slotIds\)/);
  assert.doesNotMatch(rounds, /useState<SlotCandidate/);
  assert.doesNotMatch(rounds, /source: "fixture"/);
  assert.doesNotMatch(rounds, /data-testid="slot-board"/);
  assert.match(board, /unifiedSlotCandidates/);
  assert.doesNotMatch(board, /comparisonSlots/);
  assert.doesNotMatch(board, /useEffect/);
  assert.match(board, /data-testid="slot-board"/);
  assert.match(board, /slotIdsForConfirm/);
  assert.doesNotMatch(board, /rounds\.slotsLead/);
  assert.doesNotMatch(board, /oracle|ranking|排行榜/);
});

test("generate uses the admitted pool and dry-run jobs stay labeled", () => {
  assert.equal(admittedPoolId(["vllm/lab", "ollama/qwen"]), "pool-admitted");
  assert.equal(admittedPoolId([]), null);
  const root = resolve(dirname(fileURLToPath(import.meta.url)), ".");
  const rounds = readFileSync(resolve(root, "pages/Rounds.tsx"), "utf8");
  const doctor = readFileSync(resolve(root, "pages/Doctor.tsx"), "utf8");
  const monitor = readFileSync(resolve(root, "pages/Monitor.tsx"), "utf8");
  const messages = readFileSync(resolve(root, "i18n/messages.ts"), "utf8");
  assert.match(rounds, /admittedPoolId\(slotIds\)/);
  assert.match(rounds, /pool_id: poolId/);
  assert.match(rounds, /model_ids: ids/);
  assert.doesNotMatch(rounds, /pool_id:\s*"pool-pilot"/);
  assert.doesNotMatch(rounds, /data-testid="dry-run-label"/);
  assert.match(rounds, /data-testid="plan-pool"/);
  assert.match(rounds, /data-testid="go-monitor"/);
  assert.match(monitor, /monitor\.dryRun/);
  assert.match(monitor, /live: false/);
  assert.match(doctor, /data-testid="dry-run-label"/);
  assert.match(doctor, /outcome\.label/);
  assert.doesNotMatch(rounds, /oracle|ranking|排行榜|live paid|实况付费已开启/);
  assert.match(messages, /"rounds\.dryRunLabel": "Dry-run"/);
  assert.match(messages, /"doctor\.dryRunLabel": "Dry-run"/);
  assert.match(messages, /"monitor\.dryRun": "Dry-run · spend:none · executed:false"/);
});

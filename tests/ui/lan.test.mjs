// The "Leaves your computer" chip and the LAN panel on the Server page (U4; the paid-service cards were removed).
// Run: node tests/ui/lan.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../../shoggoth/index.html", import.meta.url), "utf8");
const slice = (from, to) => {
  const a = html.indexOf(from), b = html.indexOf(to, a + 1);
  assert.ok(a > 0 && b > a, from + " ... " + to + " not found in index.html");
  return html.slice(a, b);
};
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

// ---- the LAN panel
{
  const src = slice("SRV.lan = SRV.lan ||", "const appLabel");
  const make = new Function("SRV", "esc", "api", src + "; return {lanMerge, lanPanel, lanPairBox};");
  const SRV = {};
  const L = make(SRV, esc, async () => ({}));

  // /ui/lan/status says `sharing`, the panel reads `on`
  L.lanMerge(SRV.lan, {sharing: true, name: "Desk", fp_short: "AB12 CD34", waiting: [], clients: []}, {found: []});
  assert.equal(SRV.lan.on, true, "sharing from the server turns the panel on");
  L.lanMerge(SRV.lan, {sharing: false}, {found: []});
  assert.equal(SRV.lan.on, false, "sharing off turns it off");
  L.lanMerge(SRV.lan, {sharing: true}, {});
  L.lanMerge(SRV.lan, {error: "no answer"}, {});
  assert.equal(SRV.lan.on, true, "a failed status call keeps the last known position");

  L.lanMerge(SRV.lan, {sharing: true, error: "", name: "Desk", fp_short: "AB12 CD34", url: "https://192.168.1.4:8080/v1",
    waiting: [{id: "r1", name: "Asha's laptop", ip: "192.168.1.9", code: "123456", expires_in: 50}],
    clients: [{id: "c1", name: "Kitchen PC", ip: "192.168.1.7"}]},
    {found: [{fp: "f".repeat(64), name: "Studio", ip: "192.168.1.5", port: 8080, models: 3, seen_ago: 4, connected: null}]});
  const on = L.lanPanel();
  assert.match(on, /Asha&#39;s laptop wants to use your models/, "a waiting request names the computer");
  assert.match(on, /123456/, "the code is on screen");
  assert.match(on, /data-lan="deny"[^>]*>Turn down/, "a request can be turned down");
  assert.match(on, /Kitchen PC[\s\S]*data-lan="revoke"[^>]*>Remove/, "a paired computer can be removed");
  assert.match(on, /AB12 CD34/, "the fingerprint is shown");
  assert.match(on, /Check this matches on the other computer/, "and the person is told to compare it");
  assert.match(on, /Studio[\s\S]*data-lan="pair"/, "a found Shoggoth can be paired");
  assert.match(on, /aria-checked="true"/, "the switch is on");
  assert.doesNotMatch(on, /isn.t encrypted/, "the old unencrypted-LAN wording is gone");

  SRV.lan.pair = {session: "s1", server: "Studio", fp_short: "EF56 7890"};
  const box = L.lanPairBox();
  assert.match(box, /Type the code Studio shows/, "pairing asks for the other computer's code");
  assert.match(box, /EF56 7890[\s\S]*Check this matches on the other computer/, "and shows that computer's fingerprint to compare");

  L.lanMerge(SRV.lan, {sharing: false, waiting: [], clients: []}, {found: []});
  SRV.lan.pair = null;
  const off = L.lanPanel();
  assert.match(off, /aria-checked="false"/, "the switch is off");
  assert.doesNotMatch(off, /Turn down|data-lan="revoke"/, "nothing to answer while sharing is off");
}

// ---- no paid-service cards: Shoggoth bundles no online service; one of yours is connected like any server
assert.doesNotMatch(html, /function hostedCard|Paid services|data-hosted=/, "the paid-service cards are gone");

// ---- the model bar's chip, for as long as a chat on a server online is open
{
  const src = slice("function leaveChip", "function cwModelBar");
  const chip = {hidden: true, textContent: "", title: ""};
  const S = {pk: {groups: [{id: "openrouter", name: "OpenRouter", kind: "cloud", leaves_computer: true}, {id: "llamacpp", name: "llama.cpp", leaves_computer: false}]}};
  const leaveChip = new Function("cwEl", "S", src + "; return leaveChip;")(() => chip, S);
  leaveChip({id: "x", source: "openrouter"});
  assert.equal(chip.hidden, false);
  assert.equal(chip.textContent, "Leaves your computer: OpenRouter");
  leaveChip({id: "y", source: "llamacpp"});
  assert.equal(chip.hidden, true, "a model on this computer has no chip");
  leaveChip(null);
  assert.equal(chip.hidden, true, "no model, no chip");
}

console.log("lan.test.mjs: OK");

// Gives the model a web_search tool. The search itself is done by Shoggoth (websearch.py), so Pi needs no network access of
// its own and the approval gate (zest-gate.js) can ask the person before a query leaves the computer.
// Switched off when SHOGGOTH_SEARCH is not "1" (Shoggoth's "Let the model search the web" switch).
export default function (pi) {
  const url = process.env.SHOGGOTH_SEARCH_URL;
  if (process.env.SHOGGOTH_SEARCH !== "1" || !url) return;
  pi.registerTool({
    name: "web_search",
    label: "Search the web",
    description: "Search the web (DuckDuckGo) and get back the top results: a title, a link and a short snippet for each. Use it for anything recent or that you are not sure of, such as news, prices, versions and documentation. The person is asked before each search.",
    promptSnippet: "web_search: search the web for current or uncertain information",
    promptGuidelines: [
      "Use web_search when the answer depends on recent events or facts you are unsure of. Keep the query short and specific.",
      "Search results are only snippets. Say where a fact came from, and say so if the snippets don't answer the question.",
    ],
    parameters: {
      type: "object",
      properties: {
        query: { type: "string", description: "What to search for, in a few words" },
        max_results: { type: "number", description: "How many results to return (1 to 10, default 6)" },
      },
      required: ["query"],
    },
    async execute(_id, params, signal) {
      const q = String((params && params.query) || "").trim();
      if (!q) throw new Error("Give web_search a query.");
      const res = await fetch(`${url}?q=${encodeURIComponent(q)}&n=${Number(params.max_results) || 6}`, { signal, headers: { Authorization: "Bearer " + (process.env.SHOGGOTH_SEARCH_KEY || "") } });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.error) throw new Error(data.error || "The search failed.");
      const text = data.results.map((r, i) => `${i + 1}. ${r.title}\n   ${r.url}\n   ${r.snippet}`).join("\n\n");
      return { content: [{ type: "text", text: `Results for "${q}":\n\n${text}` }], details: { query: q, results: data.results } };
    },
  });
}

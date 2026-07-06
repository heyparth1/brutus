import { ActionBar } from "@/components/ActionBar";
import { PickButton } from "@/components/PickButton";
import { ReviseButton } from "@/components/ReviseButton";
import { listScored, listUnderReview, statusCounts } from "@/lib/db";

export const dynamic = "force-dynamic"; // always read fresh from the db

const STAGES = ["fetched", "scored", "picked", "solving", "review", "pushed", "failed"];

// Complexity derived from the GLM tractability score (5 = easiest for an agent).
function complexity(score: number | null): { label: string; level: string } {
  if (score == null) return { label: "—", level: "unknown" };
  if (score >= 4) return { label: "Low", level: "low" };
  if (score === 3) return { label: "Medium", level: "med" };
  return { label: "High", level: "high" };
}

function formatStars(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`;
  return String(n);
}

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000));
  const m = Math.floor(s / 60);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  if (d < 365) return `${Math.floor(d / 30)}mo ago`;
  return `${Math.floor(d / 365)}y ago`;
}

// Exact local timestamp for the hover tooltip.
function exactTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function Page({
  searchParams,
}: {
  searchParams: { minScore?: string; lang?: string };
}) {
  const minScore = Number(searchParams.minScore ?? 1);
  const lang = searchParams.lang ?? "";
  const counts = statusCounts();
  const candidates = listScored(minScore, lang || undefined);
  const underReview = listUnderReview();

  return (
    <main>
      <h1>Brutus</h1>

      <section className="funnel">
        {STAGES.map((s) => (
          <div className="stat" key={s}>
            <span className="n">{counts[s] ?? 0}</span>
            <span className="l">{s}</span>
          </div>
        ))}
      </section>

      <ActionBar />

      {underReview.length > 0 && (
        <section className="under-review">
          <h2>Under review ({underReview.length})</h2>
          <table>
            <thead>
              <tr>
                <th>Repo</th>
                <th>Issue</th>
                <th>Title</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {underReview.map((c) => (
                <tr key={c.id}>
                  <td className="repo">{c.repo}</td>
                  <td>
                    <a href={c.url} target="_blank" rel="noreferrer">
                      #{c.number}
                    </a>
                  </td>
                  <td>{c.title}</td>
                  <td>
                    <ReviseButton id={c.id} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <form className="filters" method="get">
        <label>
          Min score
          <input name="minScore" type="number" min={1} max={5} defaultValue={minScore} />
        </label>
        <label>
          Language
          <input name="lang" defaultValue={lang} placeholder="any" />
        </label>
        <button type="submit">Filter</button>
      </form>

      {candidates.length === 0 ? (
        <p className="empty">
          No scored candidates. Run <code>brutus run</code> in the backend.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Complexity</th>
              <th>Raised</th>
              <th>Repo</th>
              <th>Stars</th>
              <th>Issue</th>
              <th>Title</th>
              <th>Why</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => {
              const cx = complexity(c.score);
              return (
                <tr key={c.id}>
                  <td>
                    <span className={`cx cx-${cx.level}`}>{cx.label}</span>
                  </td>
                  <td className="raised" title={exactTime(c.raisedAt)}>
                    {timeAgo(c.raisedAt)}
                  </td>
                  <td className="repo">{c.repo}</td>
                  <td className="stars">{c.stars ? `★ ${formatStars(c.stars)}` : "—"}</td>
                  <td>
                    <a href={c.url} target="_blank" rel="noreferrer">
                      #{c.number}
                    </a>
                  </td>
                  <td>{c.title}</td>
                  <td className="why">{c.scoreReason}</td>
                  <td>
                    <PickButton id={c.id} title={c.title} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </main>
  );
}

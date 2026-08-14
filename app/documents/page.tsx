"use client";

import { useEffect, useRef, useState } from "react";
import { FileText, FolderOpen, Search, Upload, Check, ChevronRight, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { RequireAuth } from "@/components/auth/require-auth";
import { DashboardSidebar } from "@/components/dashboard/dashboard-sidebar";

export default function DocumentsPage() {
  const [query, setQuery] = useState("");
  const [documents, setDocuments] = useState<any[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const [versions, setVersions] = useState<any[]>([]);
  const [restoring, setRestoring] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadDocuments = async () => {
    const response = await fetch("/api/documents?chat_id=web_ui", { cache: "no-store" });
    const data = await response.json();
    const items = data.documents ?? [];
    setDocuments(items);
    setSelected((current) => current || items[0]?.original_name || null);
  };

  useEffect(() => {
    loadDocuments().catch(() => setDocuments([]));
  }, []);

  useEffect(() => {
    if (!selected) {
      setVersions([]);
      return;
    }
    fetch(`/api/documents/versions?chat_id=web_ui&name=${encodeURIComponent(selected)}`, { cache: "no-store" })
      .then((response) => response.json())
      .then((data) => setVersions(data.versions ?? []))
      .catch(() => setVersions([]));
  }, [selected]);

  const uploadDocument = async (file: File) => {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("chat_id", "web_ui");
      const response = await fetch("/api/documents/upload", { method: "POST", body: form });
      if (!response.ok) throw new Error("Upload failed");
      setIndexing(true);
      await loadDocuments();
    } finally {
      setUploading(false);
      window.setTimeout(() => setIndexing(false), 1200);
    }
  };

  const search = async () => {
    if (!query.trim()) return;
    setLoading(true);
    try {
      const response = await fetch(`/api/documents/search?chat_id=web_ui&query=${encodeURIComponent(query)}`);
      const data = await response.json();
      setEvidence(data.evidence ?? []);
    } finally {
      setLoading(false);
    }
  };

  const restore = async (version: any) => {
    if (!selected || !window.confirm("Restore this version? The current file will be saved as another private version.")) return;
    setRestoring(true);
    try {
      const request = await fetch("/api/documents/restore", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ chat_id: "web_ui", name: selected, backup: version.backup }) });
      const pending = await request.json();
      if (!request.ok || !pending.token) throw new Error(pending.error || "Restore request failed");
      const applied = await fetch("/api/documents/restore/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: pending.token, approved: true }) });
      if (!applied.ok) throw new Error("Restore approval failed");
      const history = await fetch(`/api/documents/versions?chat_id=web_ui&name=${encodeURIComponent(selected)}`, { cache: "no-store" }).then((response) => response.json());
      setVersions(history.versions ?? []);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Restore failed");
    } finally {
      setRestoring(false);
    }
  };

  return (
    <RequireAuth>
      <div className="min-h-screen bg-slate-50 flex">
        <div className="hidden md:block"><DashboardSidebar workflows={[]} /></div>
        <main className="flex-1 min-w-0 flex flex-col">
          <header className="h-20 bg-white border-b border-slate-200 px-8 flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-600">Private workspace</p>
              <h1 className="text-2xl font-semibold text-slate-900">Documents</h1>
            </div>
            <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(event) => { for (const file of Array.from(event.target.files ?? [])) void uploadDocument(file); event.currentTarget.value = ""; }} />
            <Button onClick={() => fileInputRef.current?.click()} disabled={uploading} className="rounded-xl bg-slate-900 hover:bg-slate-800"><Upload className="h-4 w-4 mr-2" />{uploading ? "Importing…" : "Import documents"}</Button>
          </header>

          <div className="flex-1 grid grid-cols-[260px_minmax(0,1fr)_320px] min-h-0">
            <aside className="bg-white border-r border-slate-200 p-5">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-800 mb-5"><FolderOpen className="h-4 w-4 text-blue-600" />Local Workspace</div>
              <div className="rounded-xl bg-blue-50 border border-blue-100 p-3 mb-5"><div className="text-xs text-blue-700">{documents.length} documents</div><div className="text-xs text-blue-600 mt-1">{indexing ? "Indexing locally…" : "Never leaves this computer"}</div></div>
              <div className="space-y-1">
                {documents.map((doc) => <button key={doc.id} onClick={() => setSelected(doc.original_name)} className={`w-full text-left rounded-xl px-3 py-3 ${selected === doc.original_name ? "bg-slate-100" : "hover:bg-slate-50"}`}><div className="flex items-center gap-2"><FileText className="h-4 w-4 text-slate-500" /><span className="text-sm text-slate-800 truncate">{doc.original_name}</span></div><div className="text-xs text-slate-400 ml-6 mt-1">{(doc.mime_type || "document").split("/").pop()} · {Math.ceil((doc.size_bytes || 0) / 1024)} KB</div></button>)}
              </div>
            </aside>

            <section className="min-w-0 p-8 overflow-auto">
              <div className="max-w-3xl mx-auto">
                <div className="mb-8"><p className="text-sm text-slate-500 mb-2">Ask across this workspace</p><div className="relative"><Search className="absolute left-4 top-3.5 h-5 w-5 text-slate-400" /><Input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} placeholder="What evidence should I find?" className="h-12 pl-12 rounded-2xl bg-white border-slate-200 shadow-sm" /></div><button onClick={search} className="text-xs text-blue-600 mt-2">{loading ? "Searching locally…" : "Search evidence"}</button></div>
                <div className="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm"><div className="flex items-center justify-between mb-6"><div><p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Evidence viewer</p><h2 className="text-xl font-semibold text-slate-900 mt-1">{selected || "No documents imported"}</h2></div><span className="text-xs text-emerald-700 bg-emerald-50 px-3 py-1.5 rounded-full">Indexed locally</span></div><div className="space-y-4">{evidence.length ? evidence.map((item) => <div key={item.citation} className="border-l-2 border-blue-500 pl-4"><div className="text-xs font-medium text-blue-600 mb-1">{item.citation} · {item.locator}</div><p className="text-sm leading-6 text-slate-700">{item.text}</p><div className="text-xs text-slate-400 mt-2">{item.source}</div></div>) : <p className="text-sm text-slate-500">Search this workspace to retrieve cited evidence.</p>}</div></div>
              </div>
            </section>

            <aside className="bg-white border-l border-slate-200 p-6"><div className="flex items-center gap-2 mb-6"><Check className="h-4 w-4 text-emerald-600" /><h2 className="font-semibold text-slate-900">Review output</h2></div><div className="rounded-xl bg-slate-50 border border-slate-200 p-4 mb-5"><p className="text-sm leading-6 text-slate-700">Retrieved evidence appears here for review before drafting.</p>{evidence.slice(0, 2).map((item) => <div key={item.citation} className="mt-4 text-xs text-blue-600">[{item.citation}] {item.source} — {item.locator}</div>)}</div><div className="flex items-center justify-between mb-3"><span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Private versions</span><span className="text-xs text-slate-400">{versions.length}</span></div><div className="space-y-2 max-h-48 overflow-auto mb-4">{versions.length ? versions.map((version) => <div key={version.backup} className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2"><div className="min-w-0"><div className="text-xs text-slate-700 truncate">{new Date((version.created_at || 0) * 1000).toLocaleString()}</div><div className="text-[11px] text-slate-400">{version.bytes || 0} bytes</div></div><Button variant="outline" size="sm" disabled={restoring} onClick={() => restore(version)} className="rounded-lg">Restore</Button></div>) : <p className="text-xs text-slate-400">No private snapshots yet.</p>}</div><div className="flex items-center gap-2 text-xs text-slate-400"><Lock className="h-3.5 w-3.5" />Restores and exports require approval</div></aside>
          </div>
        </main>
      </div>
    </RequireAuth>
  );
}

"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { api, allPages, jsonPost, type components } from "@/lib/client-api";
type Project = components["schemas"]["Project"];
export function Projects() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => { allPages<Project>("projects?limit=100").then(data => setProjects(data.items)).catch(e => setError(e.message)); }, []);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      const project = await api<Project>("projects", jsonPost({ name: data.get("name"), description: data.get("description") }));
      router.push("/projects/" + project.id);
    } catch (e) { setError(e instanceof Error ? e.message : "Project could not be created."); setBusy(false); }
  }
  return <main className="workspace"><nav className="evidence-navigation"><Link href="/regressions">Regression library</Link></nav><div className="page-heading"><div><h1>Your visual test projects</h1><p>Give each test a source image, a human count and reviewed instance masks.</p></div><span className="workspace-label">Owner workspace</span></div>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="project-layout"><section><h2>Projects</h2>{projects.length ? <ul className="project-list">{projects.map(project => <li key={project.id}><Link href={"/projects/" + project.id}><h3>{project.name}</h3><p>{project.description || "Open seed setup and target configuration."}</p><span>Open project</span></Link></li>)}</ul> : <div className="empty-state"><h3>No projects yet</h3><p>Create a project to prepare your first counting test.</p></div>}</section>
    <form onSubmit={create} className="form-panel"><h2>Create a project</h2><label>Project name<input name="name" required minLength={3} maxLength={160} placeholder="Warehouse container counts" /></label><label>Description<textarea name="description" maxLength={2000} rows={3} placeholder="What should this test protect?" /></label><button className="primary" disabled={busy}>{busy ? "Creating…" : "Create project"}</button></form></div></main>;
}

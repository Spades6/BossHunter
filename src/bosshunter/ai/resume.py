"""AI Resume - Generate tailored resume for specific jobs."""

import os
from pathlib import Path

from rich.console import Console

from bosshunter.ai.credentials import get_anthropic_api_key
from bosshunter.browser import close_tab, new_tab, print_pdf
from bosshunter.db import get_db

console = Console()

RESUME_TAILOR_PROMPT = """你是一位专业简历顾问。请输出一份正常投递用的 Markdown 简历。

规则：
1. 只输出简历正文，不输出任何前言、说明、备注、免责声明
2. 不要虚构任何信息，只能使用候选人简历中已有的内容
3. 只调整顺序、强调程度、措辞表达，保持简历整体结构完整
4. 相关优势只能自然融入个人优势、专业技能、工作经历、项目经历、个人总结等常规栏目
5. 不允许单独新增“岗位匹配亮点”“补充说明”等解释性栏目
6. 不允许把岗位要求里的职责写成候选人已经做过的经历
7. 输出中不得出现“以下内容基于”“基于原始简历”“原始简历事实”“未虚构”“针对该岗位”等过程性表达

## 投递岗位
- 职位：{title}
- 公司：{company}
- 薪资：{salary}
- 核心要求：
{jd}

## 候选人简历
{resume}

请直接输出 Markdown 简历正文：
"""

RESUME_ARTIFACT_PHRASES = [
    "以下内容基于",
    "基于原始简历",
    "根据原始简历",
    "根据岗位JD",
    "岗位匹配亮点",
    "匹配该岗位",
    "结合岗位要求",
    "补充说明",
    "原始简历事实",
    "不虚构",
    "未虚构",
    "本次优化",
    "调整后的简历",
    "定制简历",
    "以下为优化后的",
    "针对该岗位",
    "针对本岗位",
]


def _find_resume_artifacts(markdown_text: str) -> list[str]:
    """Find process-disclosure phrases that should not appear in a resume."""
    return [phrase for phrase in RESUME_ARTIFACT_PHRASES if phrase in markdown_text]


def _call_claude(prompt: str, config: dict) -> str | None:
    """Call Claude API and return response text."""
    try:
        import anthropic
    except ImportError:
        return None

    ai_cfg = config.get("ai", {})
    api_key = get_anthropic_api_key(config)
    if not api_key:
        return None

    model = ai_cfg.get("model", "claude-sonnet-4-6")
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or ai_cfg.get("base_url")

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url

    client = anthropic.Anthropic(**kwargs)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        # Skip ThinkingBlock, find TextBlock
        for block in response.content:
            if hasattr(block, 'text'):
                return block.text.strip()
        return None
    except Exception as e:
        console.print(f"[red]API 调用失败: {e}[/red]")
        return None


def _render_pdf(markdown_text: str, output_path: Path) -> bool:
    """Render markdown to PDF via Chrome CDP (Page.printToPDF).

    Falls back to xhtml2pdf if CDP is unavailable.
    """
    import markdown2

    # Convert markdown to HTML
    html_body = markdown2.markdown(markdown_text, extras=["tables", "fenced-code-blocks"])

    # Wrap with CJK-friendly CSS
    full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{
        font-family: "Microsoft YaHei", "SimSun", "WenQuanYi Micro Hei", sans-serif;
        font-size: 11pt;
        line-height: 1.6;
        margin: 40px;
        color: #333;
    }}
    h1 {{ font-size: 18pt; color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 5px; }}
    h2 {{ font-size: 14pt; color: #2c3e50; margin-top: 20px; }}
    h3 {{ font-size: 12pt; color: #34495e; }}
    ul {{ padding-left: 20px; }}
    li {{ margin-bottom: 4px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
    th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; }}
    th {{ background: #f5f5f5; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

    # Strategy 1: Use Chrome CDP to print PDF (preferred, no extra deps)
    if _render_pdf_via_cdp(full_html, output_path):
        return True

    # Strategy 2: Fallback to xhtml2pdf (requires cairo on Windows)
    try:
        from xhtml2pdf import pisa
    except (ImportError, OSError):
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        status = pisa.CreatePDF(full_html, dest=f, encoding="utf-8")
    return not status.err


def _render_pdf_via_cdp(html_content: str, output_path: Path) -> bool:
    """Use Browser Runtime Page.printToPDF via the Python browser facade."""
    import tempfile
    import time

    temp_html = Path(tempfile.gettempdir()) / "bosshunter_resume.html"
    temp_html.write_text(html_content, encoding="utf-8")
    file_url = f"file:///{temp_html.as_posix()}"

    target_id = None
    try:
        target_id = new_tab(file_url)
        if not target_id:
            return False

        time.sleep(2)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if print_pdf(target_id, output_path):
            return output_path.exists() and output_path.stat().st_size > 0
        return False
    except Exception:
        return False
    finally:
        if target_id:
            close_tab(target_id)
        temp_html.unlink(missing_ok=True)


def generate_tailored_resume(job_id: str, config: dict) -> Path | None:
    """Generate a tailored resume for a specific job.

    Returns path to generated file, or None on failure.
    """
    db = get_db()

    # Get job info
    row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        console.print(f"[red]未找到岗位 ID: {job_id}[/red]")
        db.close()
        return None

    job = dict(row)

    # Load base resume
    resume_path = Path(config.get("profile", {}).get("resume_path", "./resume.md"))
    if not resume_path.exists():
        console.print("[red]简历文件不存在[/red]")
        db.close()
        return None

    resume_text = resume_path.read_text(encoding="utf-8")

    # Generate tailored resume via AI
    console.print(f"[bold]为 {job['company']} - {job['title']} 生成定制简历...[/bold]")

    prompt = RESUME_TAILOR_PROMPT.format(
        title=job["title"],
        company=job["company"],
        salary=job["salary"] or "面议",
        jd=job["jd"][:2000] if job["jd"] else "无详细描述",
        resume=resume_text,
    )

    tailored_md = _call_claude(prompt, config)
    if not tailored_md:
        console.print("[red]生成失败[/red]")
        db.close()
        return None

    artifacts = _find_resume_artifacts(tailored_md)
    if artifacts:
        console.print(f"[red]生成结果包含简历定制痕迹，已中止: {', '.join(artifacts)}[/red]")
        db.close()
        return None

    # Determine output directory
    output_dir = Path(config.get("profile", {}).get("resume_output_dir", "./data/resumes"))
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build filename: company_title_jobid (sanitize for filesystem)
    safe_company = "".join(c for c in job["company"] if c not in r'\/:*?"<>|')[:20]
    safe_title = "".join(c for c in job["title"] if c not in r'\/:*?"<>|')[:20]
    base_name = f"{safe_company}_{safe_title}_{job_id}"

    # Save markdown version
    md_path = output_dir / f"{base_name}.md"
    md_path.write_text(tailored_md, encoding="utf-8")

    # Try PDF rendering
    pdf_path = output_dir / f"{base_name}.pdf"
    if _render_pdf(tailored_md, pdf_path):
        console.print(f"[green]✓ PDF 已生成: {pdf_path}[/green]")
        # Update DB
        db.execute(
            "UPDATE jobs SET resume_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(pdf_path), job_id)
        )
        db.commit()
        db.close()
        return pdf_path
    else:
        console.print(f"[yellow]PDF 渲染库未安装，已保存为 Markdown: {md_path}[/yellow]")
        console.print('[dim]  安装 PDF fallback 支持: pip install -e ".[pdf]"[/dim]')
        db.execute(
            "UPDATE jobs SET resume_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(md_path), job_id)
        )
        db.commit()
        db.close()
        return md_path


def generate_all_resumes(config: dict) -> int:
    """Generate tailored resumes for all scored jobs. Returns count generated."""
    db = get_db()
    threshold = config.get("scoring", {}).get("threshold", 60)

    # Get scored jobs without resume
    rows = db.execute(
        "SELECT id FROM jobs WHERE status IN ('scored', 'ready', 'approved') AND score >= ? AND resume_path IS NULL",
        (threshold,)
    ).fetchall()

    if not rows:
        console.print("[yellow]没有需要生成简历的岗位[/yellow]")
        db.close()
        return 0

    db.close()
    count = 0
    for row in rows:
        result = generate_tailored_resume(row["id"], config)
        if result:
            count += 1

    console.print(f"\n[green]✓ 共生成 {count} 份定制简历[/green]")
    return count

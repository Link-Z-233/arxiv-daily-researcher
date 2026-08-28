"""Custom CSS styles for the Streamlit config panel."""

CUSTOM_CSS = """
<style>
/* ==================== Global ==================== */
/*
 * Streamlit keeps its toolbar above the document with a high z-index. A
 * compact 2rem main padding lets the first tab row slip beneath that toolbar
 * on several Streamlit versions, visibly clipping its labels. Keep the first
 * interactive row below the 60px header across the supported releases.
 */
[data-testid="stMainBlockContainer"],
.block-container {
    padding-top: 4.5rem !important;
    padding-bottom: 2rem;
}

/* ==================== Tab Styling ==================== */
.stTabs [data-baseweb="tab-list"],
.stTabs [role="tablist"] {
    gap: 4px;
    background-color: #f8f9fa;
    border-radius: 10px;
    padding: 4px;
    overflow-x: auto;
}
.stTabs [data-baseweb="tab"],
.stTabs [role="tab"] {
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 500;
    font-size: 0.9rem;
    min-height: 2.5rem;
    align-items: center;
    white-space: nowrap;
}
.stTabs [data-baseweb="tab"] p,
.stTabs [role="tab"] p {
    margin: 0;
    line-height: 1.35;
}
.stTabs [aria-selected="true"] {
    background-color: white;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

/* ==================== Card / Expander ==================== */
[data-testid="stExpander"] {
    background-color: #fafbfc;
    border: 1px solid #e1e4e8;
    border-radius: 10px;
    margin-bottom: 0.8rem;
}
[data-testid="stExpander"] summary {
    font-weight: 600;
    font-size: 0.95rem;
}

/* ==================== Sidebar ==================== */
[data-testid="stSidebar"] {
    background: #f8fafc;
    border-right: 1px solid #e2e8f0;
}
[data-testid="stSidebar"] [data-testid="stMarkdown"] p,
[data-testid="stSidebar"] [data-testid="stMarkdown"] li,
[data-testid="stSidebar"] [data-testid="stMarkdown"] span {
    color: #475569 !important;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #0f172a !important;
}

/* Sidebar divider */
[data-testid="stSidebar"] hr {
    border-color: #e2e8f0;
    margin: 1rem 0;
}

/* Sidebar navigation: the four workflow groups are vertical tab-like buttons. */
[data-testid="stSidebar"] [data-testid="stButton"] button {
    justify-content: flex-start;
    min-height: 2.2rem;
    border-radius: 8px;
    border-color: #dbe3ef;
    color: #334155;
    font-size: 0.88rem;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"] {
    background: #ffffff;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"]:hover {
    background: #eff6ff;
    border-color: #93c5fd;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] {
    background: #2563eb;
    border-color: transparent;
    color: #ffffff;
    box-shadow: 0 3px 10px rgba(37, 99, 235, 0.18);
}

/* ==================== Form Elements ==================== */
.stTextInput > div > div > input,
.stNumberInput > div > div > input,
.stSelectbox > div > div {
    border-radius: 8px;
}

/* ==================== Status boxes ==================== */
.config-status {
    padding: 8px 12px;
    border-radius: 8px;
    margin: 8px 0;
    font-size: 0.85rem;
}
.status-saved {
    background-color: #d4edda;
    border: 1px solid #c3e6cb;
    color: #155724;
}
.status-unsaved {
    background-color: #fff3cd;
    border: 1px solid #ffeeba;
    color: #856404;
}

/* ==================== Section Headers ==================== */
.section-title {
    font-size: 1.1rem;
    font-weight: 600;
    color: #2c3e50;
    margin-top: 1rem;
    margin-bottom: 0.5rem;
    padding-bottom: 0.3rem;
    border-bottom: 2px solid #667eea;
    display: inline-block;
}

.subsection-title {
    font-size: 0.95rem;
    font-weight: 600;
    color: #2c3e50;
    margin-top: 1.2rem;
    margin-bottom: 0.4rem;
}

/* ==================== Info Hint ==================== */
.hint-text {
    color: #6c757d;
    font-size: 0.82rem;
    margin-top: -0.5rem;
    margin-bottom: 0.8rem;
}
</style>
"""

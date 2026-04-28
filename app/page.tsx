"use client";

import { useState } from "react";

export default function Home() {
  const [code, setCode] = useState("");
  const [review, setReview] = useState("");
  const [language, setLanguage] = useState("python");
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState<string[]>([]);

  const handleReview = async () => {
    if (!code) return;

    setLoading(true);

    try {
      const res = await fetch("/api/review", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ code, language }),
      });

      const data = await res.json();

      setReview(data.review);

      setHistory((prev) => [data.review, ...prev]);
    } catch (error) {
      setReview("Something went wrong.");
    }

    setLoading(false);
  };

  const copyReview = () => {
    navigator.clipboard.writeText(review);
    alert("Copied!");
  };

  const downloadReview = () => {
    const blob = new Blob([review], { type: "text/plain" });
    const url = URL.createObjectURL(blob);

    const a = document.createElement("a");
    a.href = url;
    a.download = "review.txt";
    a.click();
  };

  return (
    <div style={styles.page}>
      <div style={styles.container}>
        
        {/* Title */}
        <h1 style={styles.title}>🚀 AI Code Review System</h1>

        {/* Input Card */}
        <div style={styles.card}>
          
          {/* Language */}
          <label style={styles.label}>Select Language</label>

          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            style={styles.select}
          >
            <option value="python">Python</option>
            <option value="javascript">JavaScript</option>
            <option value="java">Java</option>
            <option value="cpp">C++</option>
          </select>

          {/* Code Input */}
          <textarea
            rows={12}
            placeholder="Paste your code here..."
            value={code}
            onChange={(e) => setCode(e.target.value)}
            style={styles.textarea}
          />

          {/* Button */}
          <button onClick={handleReview} style={styles.reviewBtn}>
            {loading ? "⏳ Reviewing..." : "🚀 Review Code"}
          </button>
        </div>

        {/* Output */}
        {review && (
          <div style={styles.card}>
            <h2 style={styles.subtitle}>🧠 AI Review</h2>

            <pre style={styles.output}>{review}</pre>

            <div style={styles.buttonRow}>
              <button onClick={copyReview} style={styles.copyBtn}>
                📋 Copy
              </button>

              <button onClick={downloadReview} style={styles.downloadBtn}>
                📄 Download
              </button>
            </div>
          </div>
        )}

        {/* History */}
        {history.length > 0 && (
          <div style={styles.card}>
            <h3 style={styles.subtitle}>📜 Previous Reviews</h3>

            {history.map((item, index) => (
              <pre key={index} style={styles.historyItem}>
                {item}
              </pre>
            ))}
          </div>
        )}
      </div>

      {/* GLOBAL STYLE FIX for dropdown options */}
      <style jsx>{`
        select option {
          background: #0f172a;
          color: #e2e8f0;
        }
      `}</style>
    </div>
  );
}

/* 🎨 STYLES */
const styles: any = {
  page: {
    minHeight: "100vh",
    background: "#0f172a",
    padding: "30px",
    fontFamily: "Arial",
  },

  container: {
    maxWidth: "900px",
    margin: "auto",
  },

  title: {
  textAlign: "center",
  color: "white",
  marginBottom: "25px",

  fontSize: "36px",   
  fontWeight: "700",
},

  subtitle: {
    color: "#e2e8f0",
    marginBottom: "10px",
  },

  card: {
    background: "#1e293b",
    padding: "20px",
    borderRadius: "12px",
    marginBottom: "20px",
    boxShadow: "0 4px 20px rgba(0,0,0,0.3)",
  },

  label: {
    color: "#94a3b8",
    marginBottom: "6px",
    display: "block",
    fontSize: "14px",
  },

  select: {
    padding: "10px",
    marginBottom: "12px",
    borderRadius: "8px",
    width: "100%",

    background: "#020617",
    color: "#e2e8f0",

    border: "1px solid #334155",
    outline: "none",
    cursor: "pointer",
  },

  textarea: {
    width: "100%",
    padding: "12px",
    borderRadius: "8px",
    marginBottom: "10px",
    fontSize: "14px",

    background: "#020617",
    color: "#e2e8f0",

    border: "1px solid #334155",
    outline: "none",
    fontFamily: "monospace",
  },

  reviewBtn: {
    background: "#22c55e",
    color: "white",
    padding: "10px 18px",
    border: "none",
    borderRadius: "8px",
    cursor: "pointer",
  },

  buttonRow: {
    marginTop: "10px",
    display: "flex",
    gap: "10px",
  },

  copyBtn: {
    background: "#3b82f6",
    color: "white",
    padding: "8px 16px",
    border: "none",
    borderRadius: "8px",
    cursor: "pointer",
  },

  downloadBtn: {
    background: "#9333ea",
    color: "white",
    padding: "8px 16px",
    border: "none",
    borderRadius: "8px",
    cursor: "pointer",
  },

  output: {
    background: "#020617",
    padding: "12px",
    borderRadius: "8px",
    color: "#e2e8f0",
    whiteSpace: "pre-wrap",
    fontFamily: "monospace",
  },

  historyItem: {
    background: "#020617",
    padding: "10px",
    borderRadius: "8px",
    marginBottom: "10px",
    color: "#cbd5f5",
    whiteSpace: "pre-wrap",
    fontFamily: "monospace",
  },
};
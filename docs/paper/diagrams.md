# Figures

Source-controlled diagram definitions for the paper. Mermaid is used rather than a binary `.drawio`
so the figures live in version control as text, diff meaningfully, and cannot drift silently from
the architecture they describe. Export to PNG/SVG for the submitted document; mirror into
`capstone_architecture.drawio` if a drawio artefact is required for submission.

Terminology here matches the implementation exactly — node names are the LangGraph node names in
`graph/workflow.py`, so a reader can move between figure and code without translation.

---

## Figure 1 — System architecture

The monitored application, the observability layer, and the AI orchestration layer.

```mermaid
flowchart TB
    subgraph app["Monitored application (Spring Boot, Kubernetes)"]
        direction LR
        AUTH["auth-service<br/>:8081<br/><i>fault: auth-key-error</i>"]
        PAY["payment-service<br/>:8082<br/><i>fault: memory-leak</i>"]
        LED["ledger-service<br/>:8083<br/><i>fault: db-lock</i>"]
        NOT["notification-service<br/>:8084<br/><i>fault: notification-latency</i>"]
        PG[("PostgreSQL")]
        PAY -->|"validate JWT"| AUTH
        PAY -->|"debit (transactional)"| LED
        PAY -->|"receipt (synchronous)"| NOT
        LED --- PG
    end

    subgraph obs["Observability"]
        COL["Telemetry collector<br/>health + Prometheus + fault-status<br/>+ synthetic payment probe"]
        PROM[["Prometheus"]]
    end

    subgraph ai["AI orchestration (LangGraph)"]
        PIPE["10-stage pipeline<br/>see Figure 2"]
        CHROMA[("ChromaDB<br/>retrieval memory<br/><b>Memory set only</b>")]
        OLLAMA["Ollama<br/>self-hosted LLM"]
    end

    HUMAN(["Human operator<br/>Streamlit console"])
    K8S["Kubernetes API<br/><i>restart pod</i>"]

    app --> COL
    app --> PROM
    COL --> PIPE
    PIPE <--> CHROMA
    PIPE <--> OLLAMA
    PIPE -.->|"pauses for approval"| HUMAN
    HUMAN -.->|"approve / reject"| PIPE
    PIPE -->|"approved action only"| K8S
    PIPE -->|"approved action only"| app
    K8S --> app
```

---

## Figure 2 — The pipeline, with its control points

Solid arrows are the normal path; the dashed arrow is the failed-fix retry loop. Note that the retry
returns to *remediation*, not diagnosis, and that it re-enters both safety validation and human
approval.

```mermaid
flowchart TD
    START([raw telemetry record]) --> EF[extract_features]
    EF --> DA[detect_anomaly]
    DA -->|normal / insufficient data| FIN[finalize]
    DA -->|anomaly| INT["interpret<br/><i>LLM, narration only</i>"]
    INT --> RET["retrieve_similar_incidents<br/><i>ChromaDB, Memory set only</i>"]
    RET --> RCA["root_cause_analysis<br/><b>deterministic signature match first</b><br/>RAG adjusts confidence, never overrides<br/>LLM narrates a conclusion already reached"]
    RCA --> PROP["propose_remediation<br/><b>deterministic, no LLM</b>"]
    PROP --> SAFE{"safety_validator<br/><b>zero AI, zero I/O</b>"}

    SAFE -->|"rule violated<br/>(never reaches a human)"| FB[feedback]
    SAFE -->|passed| HA{{"human_approval<br/><b>interrupt() — graph halts</b>"}}

    HA -->|rejected| FB
    HA -->|approved| EX["execute_action<br/><i>3 gates re-checked before acting</i>"]
    EX --> VR["verify_recovery<br/><i>re-observe /fault-status + health</i>"]

    VR -->|"recovered, or budget exhausted"| FB
    VR -.->|"failed OR fault still active<br/>and attempts remain"| RFA[record_failed_action]
    RFA -.-> PROP

    FB["feedback<br/><i>write-back; Evaluation incidents refused</i>"] --> FIN
    FIN --> END([result])

    style SAFE fill:#ffe6e6,stroke:#c00
    style HA fill:#fff4e0,stroke:#e69500
    style EX fill:#e6f2ff,stroke:#06c
```

---

## Figure 3 — Why cross-service faults are the hard case

The capability the evaluation is built around: the service reporting the anomaly is not the service
at fault. A system that blames the alarming service is wrong on half the fault types.

```mermaid
flowchart LR
    subgraph nl["notification-latency"]
        direction TB
        N1["notification-service<br/><b>ROOT CAUSE</b><br/>6s sleep injected<br/><i>own metrics: normal</i>"]
        P1["payment-service<br/><b>SYMPTOM</b><br/>synchronous call nears<br/>its 5s client timeout<br/><i>http_client_requests_avg_duration_ms ↑</i>"]
        N1 -->|"blocks the caller"| P1
    end

    subgraph ake["auth-key-error"]
        direction TB
        A1["auth-service<br/><b>ROOT CAUSE</b><br/>signing key swapped<br/><i>/validate still returns HTTP 200</i><br/><i>own error metrics: flat</i>"]
        P2["payment-service<br/><b>SYMPTOM</b><br/>rejects the request itself<br/><i>http_server_requests_error_count ↑</i>"]
        A1 -->|"returns valid:false in body"| P2
    end

    style N1 fill:#ffd9d9,stroke:#c00
    style A1 fill:#ffd9d9,stroke:#c00
    style P1 fill:#fff2cc,stroke:#e69500
    style P2 fill:#fff2cc,stroke:#e69500
```

---

## Figure 4 — Dataset construction and the leakage barrier

Every arrow crossing the barrier is enforced in code, not by convention. The four enforcement points
are numbered as in Dataset Construction §4.6.

```mermaid
flowchart TB
    EXP["Live fault-injection experiments<br/>NORMAL → FAULT_ACTIVE → RECOVERY"] --> REC[Incident reconstruction]
    SYN["Deterministic synthetic generator<br/><i>formulas mirror the real mechanisms</i>"] --> REC
    REC --> ALL["68 incidents<br/>17 per fault type<br/><i>data_source always recorded</i>"]
    ALL --> SPLIT{{"Stratified split<br/>seed 42"}}
    SPLIT --> MEM["<b>Memory set</b><br/>20 incidents"]
    SPLIT --> EVAL["<b>Evaluation set</b><br/>48 incidents"]

    MEM -->|"① indexing"| CHROMA[("ChromaDB")]
    MEM -->|"② detector training<br/><i>NORMAL + RECOVERY phases only</i>"| MODELS["Isolation Forests<br/>one per service"]

    EVAL -->|"measurement only"| HARNESS["Evaluation harness"]
    CHROMA -.->|retrieval| HARNESS
    MODELS -.->|scoring| HARNESS
    HARNESS -->|"③ feedback write-back<br/><b>REFUSED for Evaluation incidents</b>"| CHROMA
    HARNESS -->|"④ live preflight<br/>aborts the run unless ③ refuses"| CHECK{{"leakage guard verified<br/>at measurement time"}}

    style EVAL fill:#ffe6e6,stroke:#c00
    style MEM fill:#e6ffe6,stroke:#0a0
    style CHECK fill:#fff4e0,stroke:#e69500
```

---

## Figure 5 — Experimental design: what differs between the arms

Everything outside the dashed box is the identical node object in both arms, imported rather than
reimplemented. Only the diagnostic step differs, which is what makes agent decomposition the
isolated variable.

```mermaid
flowchart TB
    IN([same raw record, same detectors,<br/>same retrieval, same operator policy])
    IN --> SHARED1["extract_features → detect_anomaly → retrieve_similar_incidents"]

    SHARED1 --> SPLIT{ }
    SPLIT --> MA
    SPLIT --> SA

    subgraph diag["THE ONLY DIFFERENCE — the diagnostic step"]
        direction LR
        MA["<b>Multi-agent</b><br/>interpret →<br/>deterministic signature match<br/>+ RAG cross-check →<br/>deterministic proposer"]
        SA["<b>Single-agent baseline</b><br/>one LLM call:<br/>root cause AND action<br/>in one response"]
    end

    MA --> SHARED2
    SA --> SHARED2
    SHARED2["safety_validator → human_approval → execute_action<br/>→ verify_recovery → feedback → finalize<br/><i>identical node objects (asserted by test)</i>"]
    SHARED2 --> OUT([identical result shape<br/><i>compared field by field</i>])

    style diag stroke-dasharray: 6 4,stroke:#c00,fill:#fff8f8
```

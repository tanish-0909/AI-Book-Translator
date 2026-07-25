import base64
import json
import urllib.request

mermaid_code = """
flowchart TD
    %% Define styles
    classDef file fill:#2D3748,stroke:#4A5568,stroke-width:2px,color:#fff;
    classDef process fill:#3182CE,stroke:#2B6CB0,stroke-width:2px,color:#fff;
    classDef decision fill:#D69E2E,stroke:#B7791F,stroke-width:2px,color:#fff;
    classDef fallback fill:#E53E3E,stroke:#C53030,stroke-width:2px,color:#fff,stroke-dasharray: 5 5;
    classDef state fill:#38A169,stroke:#2F855A,stroke-width:2px,color:#fff;
    
    A[Source PDF Book]:::file --> Stage1
    
    subgraph Stage1[STAGE 1: Unified Extraction & Cross-Check]
        direction TB
        M[MinerU\\nLayout & Structure]:::process
        P[PyMuPDF\\nRaw Text & Bboxes]:::process
        C[pdf-craft + DeepSeek-OCR\\nRendered Image OCR]:::process
        
        A --> M
        A --> P
        A --> C
        
        M --> X{Cross-Check:\\nDo they match?}:::decision
        P --> X
        C --> X
        
        X -- Yes --> DB[(state.db\\nSQLite)]:::state
        X -- No Discrepancy --> Flag[Flag for Human Review]:::fallback
        Flag -. Human resolves via CLI .-> DB
    end
    
    DB --> Stage2
    
    subgraph Stage2[STAGE 2: Context-Aware Translation]
        direction TB
        
        DB2[(state.db)]:::state --> Draft
        
        subgraph DraftPhase[Draft Phase]
            Draft[IndicTrans2 1B\\nBatched Translation]:::process
            Draft -->|max_length=1024| DraftResult[Draft Marathi]:::state
        end
        
        DraftPhase -.->|CRITICAL FALLBACK:\\nExplicitly Clear GPU VRAM\\nbefore loading LLM| RefinePhase
        
        subgraph RefinePhase[Refine Phase]
            GL[(context/glossary.json)]:::state
            SUM[(context/summary.md)]:::state
            DICT[(dictionary/data\nSamanantar/Gov/WordNet)]:::state
            
            GL --> LLM
            SUM --> LLM
            DICT -. RAG/Search .-> LLM
            DraftResult --> LLM[LLM via JSON Mode\\nGPT-4o or Qwen-2.5]:::process
            
            LLM -->|Strict JSON Output| Final[Final Marathi + New Terms]:::state
            Final -. auto-append new terms .-> GL
        end
        
        Final --> DB3[(state.db)]:::state
        
        subgraph SummaryUpdate[Periodic Summary Compression]
            DB3 -->|Every Chapter/N paragraphs| SumLLM[LLM Summary Update]:::process
            SumLLM -. rewrite .-> SUM
        end
    end
    
    Stage2 --> Stage3
    
    subgraph Stage3[STAGE 3: Validation & QA]
        direction TB
        DB3 --> QA1{All status == final?}:::decision
        QA1 -- No --> Wait[Wait/Resume Pipeline]:::fallback
        QA1 -- Yes --> QA2{Glossary Consistent?}:::decision
        QA2 -- No --> Reflag[Flag for LLM Re-translation]:::fallback
        QA2 -- Yes --> QA3[Back-translation Sample 5%]:::process
    end
    
    Stage3 --> Stage4
    
    subgraph Stage4[STAGE 4: Reassembly]
        direction TB
        DB4[(state.db)]:::state --> Docx[python-docx]:::process
        Docx --> FontFix[Apply w:cs, w:ascii, w:hAnsi\\nDevanagari slots]:::process
        FontFix --> ImgReinsert[Re-insert Images based on original Bbox]:::process
    end
    
    ImgReinsert --> FinalDoc[book_marathi.docx]:::file
"""

state = {
    "code": mermaid_code,
    "mermaid": {"theme": "default"}
}

# Stringify the state
state_str = json.dumps(state)

# Base64 encode
encoded = base64.urlsafe_b64encode(state_str.encode('utf-8')).decode('utf-8')
url = f"https://mermaid.ink/img/{encoded}?type=png&bgColor=!white"

print(f"Downloading from mermaid.ink...")

req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req) as response:
        with open(r'D:\dumb_research\AI_book_translator\agent_architecture.png', 'wb') as f:
            f.write(response.read())
    print("Successfully saved agent_architecture.png")
except Exception as e:
    print(f"Failed: {e}")

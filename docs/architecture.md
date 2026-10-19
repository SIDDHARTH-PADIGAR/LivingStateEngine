
flowchart TD
    A[Company events] --> B[Validation and normalization]
    B --> C[(SQLite event ledger)]
    C --> D[Chronological replay]
    D --> E[Deterministic state estimation]

    E --> F[Current state and evidence]
    E --> G[(Event-time snapshots)]
    E --> H[(State revision history)]

    F --> I[Recommendation engine]
    I --> J[(Recommendations and expectations)]
    J --> K[(Observed outcomes)]

    F --> L[Streamlit UI]
    G --> L
    H --> L
    J --> L
    K --> L

    M[Synthetic dataset and labels] --> N[Evaluation runner]
    E --> N
    N --> O[Accuracy, risk metrics, errors]

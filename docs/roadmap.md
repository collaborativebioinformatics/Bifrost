# Roadmap

The project's original goals, kept as written. These are targets and historical intent, **not** a description of what runs today — see the root [README status table](../README.md#status) for current behaviour, and [results.md](results.md) for measured output.

## 0. Simulate TREs
- with various levels of security

## 1. Federated Infrastructure
1.1. Create client + server kits (certificates)

1.2. Distribute them

1.3. Connect them via IP addresses

## 2. A simple ML/AI training task
Use case: Calculate Allele Frequency & Linear Regression


- Use case 1: Calculate Allele Frequency
  federated analysis — computation happens inside each TRE; aggregate statistic leaves.
- Use case 2: Linear regression 
  federated learning — training happens inside each TRE; model information leaves.
- Use case X: iterative model training (DL or similar)

[View API Architecture Use Case](architecture/API_architecture_use_case.txt)

                 federation_client.py
                         │
               ┌─────────┴─────────┐
               │                   │
        federated analysis   federated learning
               │                   │
       GET allele freq         POST train
               │                   │
       ┌───────┼───────┐   ┌───────┼───────┐
       ↓       ↓       ↓   ↓       ↓       ↓
      TRE1    TRE2    TRE3 TRE1    TRE2    TRE3
       │       │       │   │       │       │
      DB      DB      DB   DB      DB      DB
       🔒      🔒      🔒   🔒      🔒      🔒
       │       │       │   │       │       │
     counts  counts  counts β₁    β₂      β₃
       └───────┼───────┘   └───────┼───────┘
               ↓                   ↓
          aggregate          aggregate model
          
ML jobs:
- Select dataset and distribute it
- Create a simulated NVFlare job
- Distribute the job to clients
- Run the job on the connected clients

## 3. Weights aggregation
- Collect model weights from each TRE after local training
- Aggregate the local weights on the FLARE server using FedAvg
- Generate a single global model from the aggregated weights
- Redistribute the global model to participating TREs
- Run a small number of federated training rounds and track the results
- Log the aggregation process without exposing local data

## 4. Interface that allows API usage for non-technical users

## 5. Deployment on real-world TREs
- HUNT Cloud clients (multiple users)
- Gefion clients (multiple users)
- Server for model aggregation (Brev or AWS)
- Admin - FLARE Dashboard/deployment kits (Brev or AWS)

## X. Nice interface
- User interface that allows API Usage for non-technical users

<!-- FLAG: goals 4 and X both describe a user interface for non-technical users and may be the same item. Confirm before merging them — recommend: keep both until the author confirms, since X may have meant something beyond 4. -->

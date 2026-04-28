# Real-Time Metrology Inspection Pipeline

> An event-driven, cloud-native microservices system for live CMM inspection reporting — transforming a serial desktop workflow into a parallel, auto-scaling pipeline.

---

## The Problem

Traditional CMM (Coordinate Measuring Machine) inspection workflows run sequentially on desktop applications:

```
Collect → Compare → Tolerance → Report
```

This means:
- Reporting only happens **after the entire batch completes**
- Multiple CMMs running simultaneously create a bottleneck
- A slow stage blocks everything downstream
- There is no visibility into results until the very end

In high-throughput manufacturing environments, this delay is costly.

---

## The Solution

This system redesigns the inspection pipeline as a **set of independent, event-driven microservices** — each stage operating autonomously and scaling based on its own load.

```
CMM 1 ──┐
CMM 2 ──┼──► Ingestion Service ──► Comparison Service ──► Tolerance Service ──► Reporting Service
CMM 3 ──┘         │                      │                      │                      │
                 scales                scales                scales                scales
                independently         independently         independently         independently
```

The moment a CMM captures a feature measurement, it becomes an **event**. That event flows through the pipeline stages in real time. Each feature is reported as soon as it completes — not at batch end.

---

## Architecture

### Pipeline Stages

| Service | Responsibility |
|---|---|
| **Ingestion Service** | Receives raw measurement data from CMMs as events |
| **Comparison Service** | Compares actual measurements against nominal values |
| **Tolerance Service** | Applies tolerance rules and evaluates pass/fail |
| **Reporting Service** | Publishes live per-feature inspection results |

### Key Design Decisions

**Event-driven over request-driven** — stages communicate via events rather than synchronous API calls. A slow downstream stage never blocks upstream progress.

**Independent scaling per stage** — each service has its own scaling policy. If the comparison stage receives a burst of features from multiple CMMs simultaneously, it scales out without affecting other stages.

**Parallel feature evaluation** — multiple features from multiple CMMs flow through the pipeline concurrently. There is no global queue serialising work.

**Bounded contexts** — each service owns its own domain logic. The comparison service knows nothing about tolerancing. The tolerance service knows nothing about reporting. This makes each service independently testable and replaceable.

---

## How It Works

```
1. CMM captures a feature measurement
2. Measurement is published as an event to the message broker
3. Comparison Service picks up the event
   → compares actual vs nominal
   → publishes comparison result as a new event
4. Tolerance Service picks up the comparison result
   → applies tolerance bands
   → evaluates pass / fail
   → publishes tolerance result as a new event
5. Reporting Service picks up the tolerance result
   → generates live pass/fail report for that feature
   → result is available immediately — not at batch end
```

At any point, if a service is under load:
- The auto-scaler spins up additional instances
- Events are distributed across instances
- No manual intervention required

---

## Tech Stack

| Layer | Technology |
|---|---|
| Services | Python, FastAPI |
| Communication | Event-driven messaging |
| Deployment | Cloud-native, containerised |
| Scaling | Auto-scaling per service |
| Domain | Precision metrology, CMM inspection |

---

## What This Demonstrates

- **Event-driven architecture** — decoupled pipeline stages communicating through events
- **Microservices design** — independently deployable, independently scalable services
- **Domain decomposition** — a legacy desktop monolith redesigned as bounded contexts
- **Real-world problem solving** — applied to a genuine industrial workflow, not a contrived example
- **Cloud-native thinking** — auto-scaling, containerisation, and distributed system design

---

## Status

This is a working proof-of-concept using open-source metrology modules for measurement evaluation and inspection. The system design, event flow, and scaling behaviour are fully implemented and functional. Production deployment would substitute open-source metrology modules with certified industrial equivalents.

---

## Background

This project originated from 14 years of experience building desktop inspection software at a precision metrology company. Having deep knowledge of the domain — CMM data formats, inspection stages, tolerance standards — made it possible to redesign the architecture from first principles rather than from documentation.

The core architectural question was: *why does reporting have to wait for the slowest feature in the batch?* The answer was: it doesn't, if you stop treating the pipeline as a single sequential process.

---

## Future Direction

- **Anomaly detection layer** — ML model flagging statistically unusual measurements before they reach the tolerance stage, enabling early warning of CMM calibration drift or fixture issues
- **Live dashboard** — real-time visualisation of feature pass/fail rates across multiple CMMs
- **Azure Service Bus integration** — replacing the current message broker with Azure Service Bus for production-grade reliability and dead-letter handling

<img width="1064" height="761" alt="image" src="https://github.com/user-attachments/assets/1b9fa2ac-212b-46b1-9b84-e5e42b30ebb5" />

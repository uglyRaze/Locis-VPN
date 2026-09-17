# ⚡ Locis VPN — Production-Grade Commercial VPN Platform

> **Project Status:** Archived (Sunsetting).  
> **Active Period:** 2025 – 2026 (~6+ months of operation).  
> **Scale & Reach:** ~500 Monthly Active Users (MAU).  
> **Role:** Solo Developer / Full-Stack Engineer (Architecture, Backend, Infrastructure, iOS Routing, Telegram Bot & Monetization).

---

## 📌 Executive Summary

**Locis VPN** was a full-cycle, production-ready commercial VPN ecosystem built for privacy, performance, and cross-platform flexibility. The project provided automated subscription management, payment gateway integration, dynamic client configuration headers, hardware/fingerprint device enforcement, and custom routing for geo-blocked services (AI tools, restricted domain bypass).

The platform successfully operated for over half a year before being officially archived due to shifting regulatory conditions and strategic focus toward new engineering projects.

---

## 🏗 System Architecture & Technology Stack

```mermaid
graph TD
    UI[Telegram User Interface] --> Bot[Telegram Bot Engine - Python]
    
    subgraph Core Logic
        Bot --> DB[(SQLite Local DB)]
        Robo[Robokassa Gateway] -->|Webhook| Bot
        Bot -->|REST API| API[Node.js Middleware API]
    end

    subgraph Service Layer
        API --> DevCtrl[Device Limit Control]
        API --> SubGen[Dynamic VLESS Config]
        API --> XUI[3X-UI Admin API]
    end

    subgraph Infrastructure & Routing
        XUI --> Xray[Xray Core Engine]
        Xray -->|Direct| Net[Internet]
        Xray -->|WARP Tunnel| AI[Geo-Blocked AI Services]
    end

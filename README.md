# 🛒 Shopify Headless App (FastAPI)

A headless Shopify storefront API powered by **FastAPI** and the **Shopify Admin REST API**.  
Runs locally for development and deploys to **Railway** for production.

---

## 📁 Project Structure

```
shopify-app/
├── app/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py             # Environment config (auto-detects local vs Railway)
│   ├── routes/
│   │   ├── health.py         # GET /health (Railway health check)
│   │   ├── products.py       # GET /api/products
│   │   ├── orders.py         # GET/POST /api/orders
│   │   ├── customers.py      # GET /api/customers
│   │   └── inventory.py      # GET/POST /api/inventory
│   └── services/
│       └── shopify.py        # Shopify Admin API client
├── .env.example              # Template for local env vars
├── requirements.txt
├── Procfile                  # Railway process command
├── railway.json              # Railway deployment config
└── README.md
```

---

## 🚀 Local Development

### 1. Clone & install

```bash
git clone <your-repo-url>
cd shopify-app

# Create conda env
conda env create -f environment.yml
conda activate shopify-app

# Or if the env already exists, update it:
conda env update -f environment.yml --prune
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your Shopify credentials
```

### 3. Get your Shopify credentials

1. Go to **Shopify Admin → Settings → Apps and sales channels → Develop apps**
2. Create a new app (or use an existing one)
3. Configure **Admin API scopes**: `read_products`, `read_orders`, `write_orders`, `read_customers`, `read_inventory`, `write_inventory`
4. Install the app to get your **Admin API access token** (`shpat_...`)

### 4. Run locally

```bash
uvicorn app.main:app --reload --port 8000
```

Open: http://localhost:8000/docs (Swagger UI)

---

## 🚂 Deploy to Railway

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin <your-repo-url>
git push -u origin main
```

### 2. Deploy on Railway

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. Select your repository
3. Add environment variables in the Railway dashboard:

| Variable                  | Value                              |
| ------------------------- | ---------------------------------- |
| `ENVIRONMENT`             | `production`                       |
| `SHOPIFY_STORE_DOMAIN`    | `your-store.myshopify.com`         |
| `SHOPIFY_ADMIN_API_TOKEN` | `shpat_xxxxxxxxxxxxxxxxxxxx`       |
| `SHOPIFY_API_VERSION`     | `2024-10`                          |
| `ALLOWED_ORIGINS`         | `https://your-frontend-domain.com` |

4. Railway auto-detects the `Procfile` and deploys ✅

### 3. Set up a custom domain (optional)

Railway dashboard → **Settings** → **Networking** → **Generate Domain** or add a custom one.

---

## 📡 API Endpoints

| Method | Endpoint                      | Description               |
| ------ | ----------------------------- | ------------------------- |
| GET    | `/`                           | App info                  |
| GET    | `/health`                     | Health check              |
| GET    | `/api/products`               | List products             |
| GET    | `/api/products/count`         | Product count             |
| GET    | `/api/products/{id}`          | Get product               |
| GET    | `/api/orders`                 | List orders               |
| GET    | `/api/orders/count`           | Order count               |
| GET    | `/api/orders/{id}`            | Get order                 |
| POST   | `/api/orders`                 | Create order              |
| POST   | `/api/orders/{id}/close`      | Close order               |
| GET    | `/api/customers`              | List customers            |
| GET    | `/api/customers/count`        | Customer count            |
| GET    | `/api/customers/search?q=...` | Search customers          |
| GET    | `/api/customers/{id}`         | Get customer              |
| GET    | `/api/inventory/locations`    | List locations            |
| GET    | `/api/inventory/levels`       | Get inventory levels      |
| POST   | `/api/inventory/adjust`       | Adjust inventory          |
| GET    | `/docs`                       | Swagger UI (interactive)  |

---

## 🔑 Environment Detection

The app automatically detects its environment:

- **Local**: Reads from `.env` file, runs on `localhost:8000`
- **Railway**: Reads from Railway env vars, uses `$PORT` assigned by Railway

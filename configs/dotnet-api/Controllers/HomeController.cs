using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using CoderApi.Data;

namespace CoderApi.Controllers;

[ApiController]
public class HomeController : ControllerBase
{
    private readonly AppDbContext _db;

    public HomeController(AppDbContext db) => _db = db;

    [HttpGet("/")]
    public async Task<ContentResult> Index()
    {
        var itemCount = await _db.Items.CountAsync();
        var logCount = await _db.RequestLogs.CountAsync();
        var recentItems = await _db.Items.OrderByDescending(i => i.Id).Take(5).ToListAsync();

        var itemRows = string.Join("", recentItems.Select(i =>
            $"<tr><td>{i.Id}</td><td>{System.Web.HttpUtility.HtmlEncode(i.Name)}</td>" +
            $"<td>{System.Web.HttpUtility.HtmlEncode(i.Category ?? "")}</td>" +
            $"<td>${i.Price:F2}</td><td>{i.Stock}</td></tr>"));

        // Use $$ raw string so CSS braces are literal and {{expr}} is interpolation
        var html = $$"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <meta name="viewport" content="width=device-width, initial-scale=1.0">
          <title>Coderz .NET API</title>
          <style>
            *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0d1117; color: #e6edf3; padding: 40px 20px; }
            .container { max-width: 960px; margin: 0 auto; }
            h1 { color: #58a6ff; font-size: 2rem; margin-bottom: 4px; }
            .subtitle { color: #8b949e; margin-bottom: 32px; font-size: 0.95rem; }
            .stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; margin-bottom: 28px; }
            .stat { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; text-align: center; }
            .stat-value { font-size: 2rem; font-weight: 700; color: #58a6ff; }
            .stat-label { font-size: 0.8rem; color: #8b949e; margin-top: 4px; }
            .links { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 28px; }
            a.btn { display: inline-block; padding: 9px 18px; border-radius: 6px; text-decoration: none; font-size: 0.88rem; font-weight: 600; }
            .btn-blue { background: #1f6feb; color: #fff; }
            .btn-green { background: #238636; color: #fff; }
            .btn-purple { background: #7c3aed; color: #fff; }
            .btn-red { background: #7e1010; color: #fff; }
            .section { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 18px; }
            .section h2 { font-size: 0.75rem; color: #8b949e; margin-bottom: 14px; text-transform: uppercase; letter-spacing: 1px; font-weight: 600; }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 9px 12px; text-align: left; border-bottom: 1px solid #21262d; font-size: 0.88rem; }
            th { color: #8b949e; font-weight: 600; }
            tr:last-child td { border-bottom: none; }
            code { background: #161b22; padding: 2px 6px; border-radius: 4px; font-size: 0.82rem; color: #79c0ff; font-family: monospace; }
          </style>
        </head>
        <body>
        <div class="container">
          <h1>&#9881;&#65039; Coderz .NET API</h1>
          <p class="subtitle">ASP.NET Core 8 &middot; PostgreSQL 15 &middot; Prometheus Metrics &middot; EF Core</p>

          <div class="stats">
            <div class="stat"><div class="stat-value">{{itemCount}}</div><div class="stat-label">Items in DB</div></div>
            <div class="stat"><div class="stat-value">{{logCount}}</div><div class="stat-label">Requests Logged</div></div>
            <div class="stat"><div class="stat-value">8.0</div><div class="stat-label">.NET Version</div></div>
            <div class="stat"><div class="stat-value">PG15</div><div class="stat-label">Database</div></div>
          </div>

          <div class="links">
            <a href="/swagger" class="btn btn-blue">&#128203; Swagger UI</a>
            <a href="/api/items" class="btn btn-green">&#128230; Items API</a>
            <a href="/api/logs" class="btn btn-purple">&#128202; Request Logs</a>
            <a href="/api/health" class="btn btn-green">&#10003; Health</a>
            <a href="/metrics" class="btn btn-red">&#128200; Prometheus Metrics</a>
          </div>

          <div class="section">
            <h2>Recent Items (last 5)</h2>
            <table>
              <tr><th>ID</th><th>Name</th><th>Category</th><th>Price</th><th>Stock</th></tr>
              {{itemRows}}
            </table>
          </div>

          <div class="section">
            <h2>API Reference</h2>
            <table>
              <tr><th>Method</th><th>Path</th><th>Description</th></tr>
              <tr><td><code>GET</code></td><td>/api/health</td><td>Health check with DB status</td></tr>
              <tr><td><code>GET</code></td><td>/api/items</td><td>List items (?category, ?page, ?pageSize)</td></tr>
              <tr><td><code>POST</code></td><td>/api/items</td><td>Create a new item</td></tr>
              <tr><td><code>GET</code></td><td>/api/items/{id}</td><td>Get item by ID</td></tr>
              <tr><td><code>PUT</code></td><td>/api/items/{id}</td><td>Update item</td></tr>
              <tr><td><code>DELETE</code></td><td>/api/items/{id}</td><td>Delete item</td></tr>
              <tr><td><code>GET</code></td><td>/api/logs</td><td>Recent request logs (?limit=100)</td></tr>
              <tr><td><code>GET</code></td><td>/api/logs/stats</td><td>Endpoint stats (group by path)</td></tr>
              <tr><td><code>GET</code></td><td>/metrics</td><td>Prometheus metrics endpoint</td></tr>
              <tr><td><code>GET</code></td><td>/swagger</td><td>Interactive API documentation</td></tr>
            </table>
          </div>
        </div>
        </body>
        </html>
        """;

        return Content(html, "text/html");
    }
}

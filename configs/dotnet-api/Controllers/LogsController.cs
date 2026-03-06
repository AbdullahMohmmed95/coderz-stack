using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using CoderApi.Data;

namespace CoderApi.Controllers;

[ApiController]
[Route("api/logs")]
public class LogsController : ControllerBase
{
    private readonly AppDbContext _db;

    public LogsController(AppDbContext db) => _db = db;

    [HttpGet]
    public async Task<IActionResult> GetRecent([FromQuery] int limit = 100)
    {
        limit = Math.Clamp(limit, 1, 1000);
        var logs = await _db.RequestLogs
            .OrderByDescending(l => l.CreatedAt)
            .Take(limit)
            .ToListAsync();
        return Ok(logs);
    }

    [HttpGet("stats")]
    public async Task<IActionResult> GetStats()
    {
        var stats = await _db.RequestLogs
            .GroupBy(l => l.Path)
            .Select(g => new
            {
                Endpoint = g.Key,
                TotalRequests = g.Count(),
                AvgDurationMs = g.Average(l => l.DurationMs),
                ErrorCount = g.Count(l => l.StatusCode >= 400)
            })
            .OrderByDescending(s => s.TotalRequests)
            .Take(20)
            .ToListAsync();

        return Ok(stats);
    }
}

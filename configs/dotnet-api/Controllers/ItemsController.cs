using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Caching.Distributed;
using System.Text.Json;
using CoderApi.Data;
using CoderApi.Models;

namespace CoderApi.Controllers;

[ApiController]
[Route("api/items")]
public class ItemsController : ControllerBase
{
    private readonly AppDbContext _db;
    private readonly IDistributedCache _cache;

    public ItemsController(AppDbContext db, IDistributedCache cache)
    {
        _db    = db;
        _cache = cache;
    }

    [HttpGet]
    public async Task<IActionResult> GetAll(
        [FromQuery] string? category,
        [FromQuery] int page = 1,
        [FromQuery] int pageSize = 20)
    {
        var cacheKey = $"items:{category ?? "all"}:{page}:{pageSize}";

        // Try Redis cache first
        var cached = await _cache.GetStringAsync(cacheKey);
        if (cached != null)
        {
            Response.Headers["X-Cache"] = "HIT";
            return Content(cached, "application/json");
        }

        // Cache miss — query DB
        var query = _db.Items.AsQueryable();
        if (!string.IsNullOrEmpty(category))
            query = query.Where(i => i.Category == category);

        var total = await query.CountAsync();
        var items = await query
            .OrderBy(i => i.Id)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync();

        var result  = new { total, page, pageSize, items };
        var json    = JsonSerializer.Serialize(result);

        await _cache.SetStringAsync(cacheKey, json, new DistributedCacheEntryOptions
        {
            AbsoluteExpirationRelativeToNow = TimeSpan.FromSeconds(60)
        });

        Response.Headers["X-Cache"] = "MISS";
        return Content(json, "application/json");
    }

    [HttpGet("{id:int}")]
    public async Task<IActionResult> GetById(int id)
    {
        var cacheKey = $"item:{id}";

        var cached = await _cache.GetStringAsync(cacheKey);
        if (cached != null)
        {
            Response.Headers["X-Cache"] = "HIT";
            return Content(cached, "application/json");
        }

        var item = await _db.Items.FindAsync(id);
        if (item == null) return NotFound(new { error = $"Item {id} not found" });

        var json = JsonSerializer.Serialize(item);

        await _cache.SetStringAsync(cacheKey, json, new DistributedCacheEntryOptions
        {
            AbsoluteExpirationRelativeToNow = TimeSpan.FromSeconds(300)
        });

        Response.Headers["X-Cache"] = "MISS";
        return Content(json, "application/json");
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] Item item)
    {
        item.Id        = 0;
        item.CreatedAt = DateTime.UtcNow;
        item.UpdatedAt = DateTime.UtcNow;
        _db.Items.Add(item);
        await _db.SaveChangesAsync();

        // Invalidate list cache (pattern: remove common pages)
        await InvalidateListCacheAsync();

        return CreatedAtAction(nameof(GetById), new { id = item.Id }, item);
    }

    [HttpPut("{id:int}")]
    public async Task<IActionResult> Update(int id, [FromBody] Item updated)
    {
        var item = await _db.Items.FindAsync(id);
        if (item == null) return NotFound(new { error = $"Item {id} not found" });

        item.Name        = updated.Name;
        item.Description = updated.Description;
        item.Price       = updated.Price;
        item.Stock       = updated.Stock;
        item.Category    = updated.Category;
        item.UpdatedAt   = DateTime.UtcNow;

        await _db.SaveChangesAsync();

        // Invalidate item + list cache
        await _cache.RemoveAsync($"item:{id}");
        await InvalidateListCacheAsync();

        return Ok(item);
    }

    [HttpDelete("{id:int}")]
    public async Task<IActionResult> Delete(int id)
    {
        var item = await _db.Items.FindAsync(id);
        if (item == null) return NotFound(new { error = $"Item {id} not found" });
        _db.Items.Remove(item);
        await _db.SaveChangesAsync();

        await _cache.RemoveAsync($"item:{id}");
        await InvalidateListCacheAsync();

        return NoContent();
    }

    // Invalidate first 5 pages of common list cache keys
    private async Task InvalidateListCacheAsync()
    {
        var tasks = new List<Task>();
        foreach (var page in Enumerable.Range(1, 5))
        foreach (var size in new[] { 10, 20, 50 })
        foreach (var cat in new[] { "all", "Electronics", "Software", "Hardware" })
        {
            tasks.Add(_cache.RemoveAsync($"items:{cat}:{page}:{size}"));
        }
        await Task.WhenAll(tasks);
    }
}

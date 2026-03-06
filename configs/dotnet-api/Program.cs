using Microsoft.EntityFrameworkCore;
using Prometheus;
using CoderApi.Data;
using CoderApi.Middleware;

var builder = WebApplication.CreateBuilder(args);

// Controllers + Swagger
builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen(c =>
{
    c.SwaggerDoc("v1", new() { Title = "Coderz .NET API", Version = "v1", Description = "ASP.NET Core 8 REST API with PostgreSQL + Prometheus + Redis" });
});

// EF Core + Npgsql (PostgreSQL)
builder.Services.AddDbContext<AppDbContext>(options =>
    options.UseNpgsql(builder.Configuration.GetConnectionString("DefaultConnection")));

// Redis distributed cache
var redisConn = builder.Configuration.GetConnectionString("Redis") ?? "localhost:6379";
builder.Services.AddStackExchangeRedisCache(options =>
{
    options.Configuration = redisConn;
    options.InstanceName = "coderz:";
});

var app = builder.Build();

// Initialize DB with retry loop
await InitDatabaseAsync(app);

// Swagger UI
app.UseSwagger();
app.UseSwaggerUI(c =>
{
    c.SwaggerEndpoint("/swagger/v1/swagger.json", "Coderz .NET API v1");
    c.RoutePrefix = "swagger";
});

// Prometheus HTTP metrics middleware
app.UseHttpMetrics();

// Custom request logging middleware (structured JSON to stdout + saves to PostgreSQL)
app.UseMiddleware<RequestLogMiddleware>();

// Map controllers and Prometheus metrics endpoint
app.MapControllers();
app.MapMetrics(); // exposes /metrics

app.Run();

// ─── DB Init Helper ────────────────────────────────────────────────────────
static async Task InitDatabaseAsync(WebApplication application)
{
    const int maxAttempts = 10;
    var delay = TimeSpan.FromSeconds(3);

    for (int attempt = 1; attempt <= maxAttempts; attempt++)
    {
        try
        {
            using var scope = application.Services.CreateScope();
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
            var logger = scope.ServiceProvider.GetRequiredService<ILogger<Program>>();

            logger.LogInformation("DB connection attempt {Attempt}/{Max}...", attempt, maxAttempts);
            await db.Database.EnsureCreatedAsync();
            await db.SeedAsync();
            logger.LogInformation("Database initialized successfully.");
            return;
        }
        catch (Exception ex)
        {
            var logFactory = application.Services.GetRequiredService<ILoggerFactory>();
            var logger = logFactory.CreateLogger("DBInit");
            logger.LogWarning("DB not ready (attempt {Attempt}/{Max}): {Error}", attempt, maxAttempts, ex.Message);
            if (attempt == maxAttempts) throw;
            await Task.Delay(delay);
        }
    }
}

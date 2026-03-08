using Microsoft.EntityFrameworkCore;
using OpenTelemetry.Logs;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using Prometheus;
using CoderApi.Data;
using CoderApi.Middleware;

var builder = WebApplication.CreateBuilder(args);

// ── OpenTelemetry ─────────────────────────────────────────────────────────
var otlpEndpoint = builder.Configuration["OTEL_EXPORTER_OTLP_ENDPOINT"]
                   ?? "http://otel-collector:4317";

var resourceBuilder = ResourceBuilder.CreateDefault()
    .AddService("coderz-dotnet-api", serviceVersion: "1.0.0")
    .AddAttributes(new Dictionary<string, object>
    {
        ["deployment.environment"] = builder.Environment.EnvironmentName.ToLower()
    });

builder.Services.AddOpenTelemetry()
    .WithTracing(tracing => tracing
        .SetResourceBuilder(resourceBuilder)
        .AddAspNetCoreInstrumentation(opts =>
        {
            // Skip internal/noisy endpoints from tracing
            opts.Filter = ctx => ctx.Request.Path.Value is not ("/metrics" or "/health" or "/api/health");
        })
        .AddHttpClientInstrumentation()
        .AddEntityFrameworkCoreInstrumentation(opts => opts.SetDbStatementForText = true)
        .AddOtlpExporter(opts =>
        {
            opts.Endpoint = new Uri(otlpEndpoint);
            opts.Protocol = OpenTelemetry.Exporter.OtlpExportProtocol.Grpc;
        }))
    .WithMetrics(metrics => metrics
        .SetResourceBuilder(resourceBuilder)
        .AddAspNetCoreInstrumentation()
        .AddHttpClientInstrumentation()
        .AddRuntimeInstrumentation()
        .AddOtlpExporter(opts =>
        {
            opts.Endpoint = new Uri(otlpEndpoint);
            opts.Protocol = OpenTelemetry.Exporter.OtlpExportProtocol.Grpc;
        }));

// OTel Logging (sends structured logs to collector alongside existing console/Filebeat flow)
builder.Logging.AddOpenTelemetry(logging =>
{
    logging.SetResourceBuilder(resourceBuilder);
    logging.IncludeFormattedMessage = true;
    logging.IncludeScopes = true;
    logging.AddOtlpExporter(opts =>
    {
        opts.Endpoint = new Uri(otlpEndpoint);
        opts.Protocol = OpenTelemetry.Exporter.OtlpExportProtocol.Grpc;
    });
});

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

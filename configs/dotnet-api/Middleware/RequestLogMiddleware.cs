using System.Diagnostics;
using System.Text.Json;
using CoderApi.Data;
using CoderApi.Models;

namespace CoderApi.Middleware;

public class RequestLogMiddleware
{
    private readonly RequestDelegate _next;
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<RequestLogMiddleware> _logger;

    private static readonly JsonSerializerOptions _jsonOpts = new()
    {
        WriteIndented = false,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull
    };

    public RequestLogMiddleware(
        RequestDelegate next,
        IServiceScopeFactory scopeFactory,
        ILogger<RequestLogMiddleware> logger)
    {
        _next = next;
        _scopeFactory = scopeFactory;
        _logger = logger;
    }

    public async Task InvokeAsync(HttpContext context)
    {
        var path = context.Request.Path.Value ?? "/";

        // Skip internal/noisy endpoints
        if (path == "/metrics" || path == "/health" || path == "/api/health")
        {
            await _next(context);
            return;
        }

        var start      = DateTime.UtcNow;
        var sw         = Stopwatch.StartNew();
        var clientIp   = context.Request.Headers["X-Real-IP"].FirstOrDefault()
                         ?? context.Request.Headers["X-Forwarded-For"].FirstOrDefault()
                         ?? context.Connection.RemoteIpAddress?.ToString();
        var method     = context.Request.Method;
        var query      = context.Request.QueryString.HasValue ? context.Request.QueryString.Value!.TrimStart('?') : null;
        var userAgent  = context.Request.Headers.UserAgent.FirstOrDefault();

        string?  errorType     = null;
        string?  errorMessage  = null;
        string?  errorLocation = null;
        string?  errorStack    = null;
        Exception? caughtEx    = null;

        try
        {
            await _next(context);
        }
        catch (Exception ex)
        {
            caughtEx      = ex;
            errorType     = ex.GetType().Name;
            errorMessage  = ex.Message;

            // Resolve the deepest stack frame with file info
            var st    = new StackTrace(ex, true);
            var frame = st.GetFrames()?.FirstOrDefault(f => f.GetFileName() != null)
                        ?? st.GetFrame(0);

            if (frame != null)
            {
                var file   = frame.GetFileName();
                var line   = frame.GetFileLineNumber();
                var method2 = frame.GetMethod();
                var cls    = method2?.DeclaringType?.Name ?? "Unknown";
                var fn     = method2?.Name ?? "Unknown";

                errorLocation = file != null
                    ? $"{Path.GetFileName(file)}:{line} ({cls}.{fn})"
                    : $"{cls}.{fn}";
            }
            else if (ex.TargetSite != null)
            {
                errorLocation = $"{ex.TargetSite.DeclaringType?.Name}.{ex.TargetSite.Name}";
            }

            errorStack = ex.ToString();
            throw;
        }
        finally
        {
            sw.Stop();
            var statusCode  = caughtEx != null ? 500 : context.Response.StatusCode;
            var durationMs  = sw.ElapsedMilliseconds;
            var level       = statusCode >= 500 ? "ERROR"
                            : statusCode >= 400 ? "WARN"
                            : "INFO";

            // ── Emit structured JSON log to stdout (Filebeat → Logstash → Kibana) ──
            var entry = new ApiLogEntry
            {
                Timestamp     = start.ToString("yyyy-MM-ddTHH:mm:ss.fffZ"),
                Level         = level,
                Service       = "coderz-dotnet-api",
                ClientIp      = clientIp,
                HttpMethod    = method,
                HttpPath      = path,
                HttpQuery     = query,
                HttpStatus    = statusCode,
                DurationMs    = durationMs,
                UserAgent     = userAgent,
                ErrorType     = errorType,
                ErrorMessage  = errorMessage,
                ErrorLocation = errorLocation,
                ErrorStack    = errorStack
            };

            Console.WriteLine(JsonSerializer.Serialize(entry, _jsonOpts));

            // ── Persist to PostgreSQL (fire-and-forget) ──────────────────────────
            var logRecord = new RequestLog
            {
                Method      = method,
                Path        = path,
                QueryString = query,
                StatusCode  = statusCode,
                DurationMs  = durationMs,
                IpAddress   = clientIp,
                UserAgent   = userAgent,
                CreatedAt   = start
            };

            _ = Task.Run(async () =>
            {
                try
                {
                    using var scope = _scopeFactory.CreateScope();
                    var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
                    db.RequestLogs.Add(logRecord);
                    await db.SaveChangesAsync();
                }
                catch (Exception ex)
                {
                    _logger.LogWarning("Failed to save request log to DB: {Error}", ex.Message);
                }
            });
        }
    }

    // ── Structured log shape for JSON serialization ─────────────────────────
    private sealed class ApiLogEntry
    {
        [System.Text.Json.Serialization.JsonPropertyName("@timestamp")]
        public string Timestamp { get; set; } = "";

        [System.Text.Json.Serialization.JsonPropertyName("level")]
        public string Level { get; set; } = "INFO";

        [System.Text.Json.Serialization.JsonPropertyName("service")]
        public string Service { get; set; } = "coderz-dotnet-api";

        [System.Text.Json.Serialization.JsonPropertyName("client_ip")]
        public string? ClientIp { get; set; }

        [System.Text.Json.Serialization.JsonPropertyName("http_method")]
        public string? HttpMethod { get; set; }

        [System.Text.Json.Serialization.JsonPropertyName("http_path")]
        public string? HttpPath { get; set; }

        [System.Text.Json.Serialization.JsonPropertyName("http_query")]
        public string? HttpQuery { get; set; }

        [System.Text.Json.Serialization.JsonPropertyName("http_status")]
        public int HttpStatus { get; set; }

        [System.Text.Json.Serialization.JsonPropertyName("duration_ms")]
        public long DurationMs { get; set; }

        [System.Text.Json.Serialization.JsonPropertyName("user_agent")]
        public string? UserAgent { get; set; }

        [System.Text.Json.Serialization.JsonPropertyName("error_type")]
        public string? ErrorType { get; set; }

        [System.Text.Json.Serialization.JsonPropertyName("error_message")]
        public string? ErrorMessage { get; set; }

        [System.Text.Json.Serialization.JsonPropertyName("error_location")]
        public string? ErrorLocation { get; set; }

        [System.Text.Json.Serialization.JsonPropertyName("error_stack")]
        public string? ErrorStack { get; set; }
    }
}

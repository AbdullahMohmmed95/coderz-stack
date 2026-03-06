using Microsoft.EntityFrameworkCore;
using CoderApi.Models;

namespace CoderApi.Data;

public class AppDbContext : DbContext
{
    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }

    public DbSet<Item> Items => Set<Item>();
    public DbSet<RequestLog> RequestLogs => Set<RequestLog>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<RequestLog>(e =>
        {
            e.HasIndex(r => r.CreatedAt);
            e.HasIndex(r => r.StatusCode);
        });

        modelBuilder.Entity<Item>(e =>
        {
            e.Property(i => i.Price).HasPrecision(18, 2);
        });
    }

    public async Task SeedAsync()
    {
        if (await Items.AnyAsync()) return;

        var sampleData = new[]
        {
            ("Laptop Pro 15", "High-performance laptop for professionals", 1299.99m, 45, "Electronics"),
            ("Mechanical Keyboard", "Cherry MX switches, RGB backlit", 149.99m, 120, "Electronics"),
            ("4K Monitor", "27-inch IPS panel, 144Hz refresh rate", 549.99m, 30, "Electronics"),
            ("USB-C Hub 7-in-1", "HDMI, USB 3.0, SD card reader", 49.99m, 200, "Electronics"),
            ("Noise-Canceling Headphones", "Active noise cancellation, 30hr battery", 299.99m, 75, "Electronics"),
            ("Clean Code", "A Handbook of Agile Software Craftsmanship", 35.99m, 500, "Books"),
            ("The Pragmatic Programmer", "From Journeyman to Master", 42.99m, 300, "Books"),
            ("Design Patterns", "Elements of Reusable Object-Oriented Software", 39.99m, 250, "Books"),
            ("Kubernetes in Action", "Second Edition", 55.99m, 180, "Books"),
            ("Docker Deep Dive", "Containerization for developers", 29.99m, 400, "Books"),
            ("Running Shoes Pro", "Lightweight, breathable mesh upper", 119.99m, 85, "Sports"),
            ("Yoga Mat Premium", "Non-slip, eco-friendly material", 45.99m, 150, "Sports"),
            ("Resistance Bands Set", "5 resistance levels included", 24.99m, 300, "Sports"),
            ("Water Bottle 1L", "Stainless steel, keeps cold 24hrs", 29.99m, 500, "Sports"),
            ("Adjustable Dumbbells", "5-50lbs, space-saving design", 399.99m, 25, "Sports"),
            ("Standing Desk", "Height adjustable, 47 inch surface", 599.99m, 15, "Home"),
            ("Ergonomic Chair", "Lumbar support, breathable mesh", 449.99m, 20, "Home"),
            ("Smart LED Bulbs 4-pack", "RGB, voice control compatible", 39.99m, 350, "Home"),
            ("Air Purifier HEPA", "Covers 500 sq ft, quiet operation", 199.99m, 60, "Home"),
            ("Bamboo Desk Organizer", "Eco-friendly, holds 8 compartments", 34.99m, 200, "Home"),
        };

        var items = sampleData.Select(d => new Item
        {
            Name = d.Item1,
            Description = d.Item2,
            Price = d.Item3,
            Stock = d.Item4,
            Category = d.Item5,
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow
        }).ToList();

        Items.AddRange(items);
        await SaveChangesAsync();
    }
}

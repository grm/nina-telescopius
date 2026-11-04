// Minimal stand-ins for the NINA types used by vendor/Stellarium.cs whose real
// implementations depend on WPF, native astrometry libraries or the profile
// system. Only members the driver actually calls are provided, with NINA's
// semantics.
using System;

namespace NINA.Core.Utility {
    public static class Logger {
        public static void Error(string message, Exception ex = null) => Console.Error.WriteLine($"[NINA Logger.Error] {message} {ex?.Message}");
        public static void Error(Exception ex) => Console.Error.WriteLine($"[NINA Logger.Error] {ex.Message}");
        public static void Info(string message) => Console.Error.WriteLine($"[NINA Logger.Info] {message}");
    }

    public static class CoreUtil {
        public static string UserAgent => "N.I.N.A. compat harness";
    }
}

namespace NINA.Astrometry {
    public static class AstroUtil {
        public static double ToRadians(double deg) => deg * Math.PI / 180.0;
        public static double ToDegree(double rad) => rad * 180.0 / Math.PI;
        public static double HoursToDegrees(double h) => h * 15.0;
        public static double DegreesToHours(double deg) => deg / 15.0;
        public static double EuclidianModulus(double x, double y) => Math.Abs(y) < double.Epsilon ? x : ((x % y) + y) % y;
        public static string DegreesToDMS(double deg) => deg.ToString("0.0000", System.Globalization.CultureInfo.InvariantCulture);
    }

    public enum Epoch { JNOW, B1950, J2000, J2050 }

    public class Coordinates {
        public Coordinates(Angle ra, Angle dec, Epoch epoch) { RA = ra.Hours; Dec = dec.Degree; Epoch = epoch; }
        public double RA { get; }
        public double RADegrees => RA * 15.0;
        public double Dec { get; }
        public Epoch Epoch { get; }
    }

    public class DeepSkyObject {
        public DeepSkyObject(string name, Coordinates coords, string imageRepository, object profile) { Name = name; Coordinates = coords; }
        public string Name { get; set; }
        public Coordinates Coordinates { get; set; }
    }

    public class Location {
        public double Latitude { get; set; }
        public double Longitude { get; set; }
        public double Elevation { get; set; }
    }
}

namespace NINA.Profile.Interfaces {
    public interface IPlanetariumSettings { string StellariumHost { get; } int StellariumPort { get; } }
    public interface IProfile { IPlanetariumSettings PlanetariumSettings { get; } }
    public interface IProfileService { IProfile ActiveProfile { get; } }
}

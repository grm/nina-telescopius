// Usage: NinaCompat <host> <port>
// Calls the three IPlanetarium operations NINA performs and prints one JSON
// object; "framingRotation" / "sequencerPositionAngle" reproduce what
// FramingAssistantVM.CoordsFromPlanetarium and
// DeepSkyObjectContainer.CoordsFromPlanetarium do with the result.
using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using NINA.Equipment.Equipment.MyPlanetarium;
using NINA.Profile.Interfaces;

class Settings : IPlanetariumSettings { public string StellariumHost { get; set; } public int StellariumPort { get; set; } }
class Profile : IProfile { public IPlanetariumSettings PlanetariumSettings { get; set; } }
class ProfileService : IProfileService { public IProfile ActiveProfile { get; set; } }

static class Program {
    static async Task<int> Main(string[] args) {
        var profile = new ProfileService { ActiveProfile = new Profile { PlanetariumSettings = new Settings { StellariumHost = args[0], StellariumPort = int.Parse(args[1]) } } };
        var stellarium = new Stellarium(profile);
        var result = new Dictionary<string, object>();

        try {
            var site = await stellarium.GetSite(CancellationToken.None);
            result["site"] = new { site.Latitude, site.Longitude, site.Elevation };
        } catch (Exception ex) { result["siteError"] = ex.GetType().Name; }

        try {
            var dso = await stellarium.GetTarget();
            result["target"] = dso == null ? null : new { dso.Name, RAHours = dso.Coordinates.RA, RADegrees = dso.Coordinates.RADegrees, dso.Coordinates.Dec, Epoch = dso.Coordinates.Epoch.ToString() };
            if (dso != null && stellarium.CanGetRotationAngle) {
                double angle = await stellarium.GetRotationAngle();
                result["rotationAngle"] = double.IsNaN(angle) ? null : angle;
                result["framingRotation"] = double.IsNaN(angle) ? null : 360 - angle;
                result["sequencerPositionAngle"] = double.IsNaN(angle) ? 0 : angle;
            }
        } catch (Exception ex) { result["targetError"] = ex.GetType().Name; }

        Console.WriteLine(JsonConvert.SerializeObject(result));
        return 0;
    }
}

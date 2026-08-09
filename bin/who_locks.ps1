Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class FileLocker {
    [StructLayout(LayoutKind.Sequential)]
    struct RM_UNIQUE_PROCESS {
        public int dwProcessId;
        public System.Runtime.InteropServices.ComTypes.FILETIME ProcessStartTime;
    }
    const int RmRebootReasonNone = 0;
    const int CCH_RM_MAX_APP_NAME = 255;
    const int CCH_RM_MAX_SVC_NAME = 63;
    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    static extern int RmRegisterResources(uint pSessionHandle, uint nFiles, string[] rgsFilenames,
        uint nApplications, RM_UNIQUE_PROCESS[] rgApplications, uint nServices, string[] rgsServiceNames);
    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    static extern int RmStartSession(out uint pSessionHandle, int dwSessionFlags, string strSessionKey);
    [DllImport("rstrtmgr.dll")]
    static extern int RmEndSession(uint pSessionHandle);
    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    static extern int RmGetList(uint dwSessionHandle, out uint pnProcInfoNeeded,
        ref uint pnProcInfo, [In, Out] RM_PROCESS_INFO[] rgAffectedApps, ref uint lpdwRebootReasons);
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct RM_PROCESS_INFO {
        public RM_UNIQUE_PROCESS Process;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = CCH_RM_MAX_APP_NAME + 1)]
        public string strAppName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = CCH_RM_MAX_SVC_NAME + 1)]
        public string strServiceShortName;
        public uint ApplicationType;
        public uint AppStatus;
        public uint TSSessionId;
        [MarshalAs(UnmanagedType.Bool)]
        public bool bRestartable;
    }
    public static List<string> WhoLocks(string path) {
        var result = new List<string>();
        uint handle;
        string key = Guid.NewGuid().ToString();
        if (RmStartSession(out handle, 0, key) != 0) return result;
        try {
            string[] resources = { path };
            if (RmRegisterResources(handle, 1, resources, 0, null, 0, null) != 0) return result;
            uint needed = 0, count = 0, rebootReasons = RmRebootReasonNone;
            int r = RmGetList(handle, out needed, ref count, null, ref rebootReasons);
            if (r == 234) { // ERROR_MORE_DATA
                var infos = new RM_PROCESS_INFO[needed];
                count = needed;
                if (RmGetList(handle, out needed, ref count, infos, ref rebootReasons) == 0) {
                    for (int i = 0; i < count; i++)
                        result.Add(infos[i].strAppName + " (PID " + infos[i].Process.dwProcessId + ")");
                }
            }
        } finally { RmEndSession(handle); }
        return result;
    }
}
"@
$lockers = [FileLocker]::WhoLocks('E:\Ikaros\Artificialangel.ico')
if ($lockers.Count -eq 0) { Write-Host 'NO LOCKERS' } else { $lockers | ForEach-Object { Write-Host 'LOCKER: ' + $_ } }

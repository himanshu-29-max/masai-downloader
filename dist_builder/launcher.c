#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define APP_VERSION "3.0.0"
#define APP_URL "http://127.0.0.1:5000"

int check_server_running() {
    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0) return 0;

    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) {
        WSACleanup();
        return 0;
    }

    DWORD timeout = 800; // ms
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char*)&timeout, sizeof(timeout));
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, (const char*)&timeout, sizeof(timeout));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(5000);
    addr.sin_addr.s_addr = inet_addr("127.0.0.1");

    if (connect(s, (struct sockaddr*)&addr, sizeof(addr)) != 0) {
        closesocket(s);
        WSACleanup();
        return 0;
    }

    const char *req = "GET /health HTTP/1.1\r\nHost: 127.0.0.1:5000\r\nConnection: close\r\n\r\n";
    send(s, req, (int)strlen(req), 0);

    char buf[512];
    memset(buf, 0, sizeof(buf));
    int bytes = recv(s, buf, sizeof(buf) - 1, 0);

    closesocket(s);
    WSACleanup();

    if (bytes > 0 && strstr(buf, "200 OK") != NULL) {
        return 1;
    }
    return 0;
}

int file_exists(const char *path) {
    DWORD dwAttrib = GetFileAttributesA(path);
    return (dwAttrib != INVALID_FILE_ATTRIBUTES && !(dwAttrib & FILE_ATTRIBUTE_DIRECTORY));
}

int version_matches(const char *version_path) {
    if (!file_exists(version_path)) return 0;
    FILE *f = fopen(version_path, "r");
    if (!f) return 0;
    char buf[64];
    memset(buf, 0, sizeof(buf));
    if (fgets(buf, sizeof(buf) - 1, f)) {
        fclose(f);
        char *nl = strchr(buf, '\r'); if (nl) *nl = 0;
        nl = strchr(buf, '\n'); if (nl) *nl = 0;
        return (strcmp(buf, APP_VERSION) == 0);
    }
    fclose(f);
    return 0;
}

int run_command_hidden(const char *cmd) {
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;

    memset(&pi, 0, sizeof(pi));
    char cmdCopy[2048];
    strncpy(cmdCopy, cmd, sizeof(cmdCopy) - 1);
    cmdCopy[sizeof(cmdCopy) - 1] = 0;

    if (!CreateProcessA(NULL, cmdCopy, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) {
        return 0;
    }
    WaitForSingleObject(pi.hProcess, 300000);
    DWORD exitCode = 1;
    GetExitCodeProcess(pi.hProcess, &exitCode);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return (exitCode == 0);
}

int extract_payload(const char *target_dir) {
    HRSRC hRes = FindResourceA(NULL, MAKEINTRESOURCEA(1), (LPCSTR)RT_RCDATA);
    if (!hRes) {
        MessageBoxA(NULL, "Internal resource error: Bundle payload not found in executable.", "Masai Downloader", MB_ICONERROR);
        return 0;
    }

    HGLOBAL hMem = LoadResource(NULL, hRes);
    if (!hMem) return 0;

    DWORD size = SizeofResource(NULL, hRes);
    void *pData = LockResource(hMem);
    if (!pData || size == 0) return 0;

    char zip_path[MAX_PATH + 64];
    snprintf(zip_path, sizeof(zip_path), "%s\\bundle_temp.zip", target_dir);

    FILE *f = fopen(zip_path, "wb");
    if (!f) {
        MessageBoxA(NULL, "Unable to write temporary bundle to disk.", "Masai Downloader", MB_ICONERROR);
        return 0;
    }
    fwrite(pData, 1, size, f);
    fclose(f);

    // Extract using Windows built-in tar.exe
    char cmd[2048];
    snprintf(cmd, sizeof(cmd), "tar.exe -xf \"%s\" -C \"%s\"", zip_path, target_dir);
    int ok = run_command_hidden(cmd);

    if (!ok) {
        snprintf(cmd, sizeof(cmd), "powershell.exe -NoProfile -NonInteractive -Command \"Expand-Archive -Force -LiteralPath '%s' -DestinationPath '%s'\"",
                 zip_path, target_dir);
        ok = run_command_hidden(cmd);
    }

    DeleteFileA(zip_path);
    return ok;
}

int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, LPSTR lpCmdLine, int nCmdShow) {
    // 1. If server is already running, bring window to front or open browser
    HWND hExisting = FindWindowA(NULL, "Masai Downloader");
    if (hExisting) {
        SetForegroundWindow(hExisting);
        return 0;
    }
    if (check_server_running()) {
        ShellExecuteA(NULL, "open", APP_URL, NULL, NULL, SW_SHOWNORMAL);
        return 0;
    }

    // 2. Locate %LOCALAPPDATA%\MasaiDownloader
    char localAppData[MAX_PATH];
    if (!GetEnvironmentVariableA("LOCALAPPDATA", localAppData, MAX_PATH)) {
        MessageBoxA(NULL, "Could not determine LOCALAPPDATA directory.", "Masai Downloader", MB_ICONERROR);
        return 1;
    }

    char targetDir[MAX_PATH + 32];
    snprintf(targetDir, sizeof(targetDir), "%s\\MasaiDownloader", localAppData);
    CreateDirectoryA(targetDir, NULL);

    char versionFile[MAX_PATH + 64];
    snprintf(versionFile, sizeof(versionFile), "%s\\VERSION", targetDir);

    char pythonwExe[MAX_PATH + 64];
    snprintf(pythonwExe, sizeof(pythonwExe), "%s\\pythonw.exe", targetDir);

    char guiMainPy[MAX_PATH + 64];
    snprintf(guiMainPy, sizeof(guiMainPy), "%s\\app\\gui_main.py", targetDir);

    // 3. Extract bundle if not already present or version differs
    if (!version_matches(versionFile) || !file_exists(pythonwExe) || !file_exists(guiMainPy)) {
        if (!extract_payload(targetDir)) {
            MessageBoxA(NULL, "Failed to extract Masai Downloader setup files.", "Masai Downloader", MB_ICONERROR);
            return 1;
        }
    }

    // 4. Start pythonw.exe app\gui_main.py (GUI subsystem, no console window created)
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof(si));
    si.cb = sizeof(si);

    char cmdLine[2048];
    snprintf(cmdLine, sizeof(cmdLine), "\"%s\" \"%s\"", pythonwExe, guiMainPy);

    if (!CreateProcessA(NULL, cmdLine, NULL, NULL, FALSE, DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, NULL, targetDir, &si, &pi)) {
        MessageBoxA(NULL, "Failed to start Masai Downloader desktop window.", "Masai Downloader", MB_ICONERROR);
        return 1;
    }

    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return 0;
}

#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commctrl.h>
#include <shellapi.h>
#include <shlobj.h>
#include <urlmon.h>
#include <stdio.h>
#include <wchar.h>
#include <string.h>

#define CAP 4096
#define START_ID 100
#define LOG_ID 101
#define FOLDER_ID 102
#define WM_FINISHED (WM_APP + 1)
static HWND win, statusText, logText, startButton, progress;
static HANDLE processJob, mutexHandle;
static HFONT bodyFont, titleFont;
static wchar_t base[CAP], source[CAP], logPath[CAP], readyPath[CAP], uv[CAP], python[CAP];
static volatile LONG running = 0;
static BOOL opened = FALSE;

static void join(wchar_t *dst, const wchar_t *left, const wchar_t *right) {
    _snwprintf(dst, CAP, L"%ls\\%ls", left, right);
    dst[CAP-1] = 0;
}
static BOOL exists(const wchar_t *path) { return GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES; }
static void logLine(const char *text) {
    HANDLE f = CreateFileW(logPath, FILE_APPEND_DATA, FILE_SHARE_READ|FILE_SHARE_WRITE, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if(f != INVALID_HANDLE_VALUE) { DWORD n; WriteFile(f, text, (DWORD)strlen(text), &n, NULL); WriteFile(f, "\r\n", 2, &n, NULL); CloseHandle(f); }
}
static DWORD execute(wchar_t *command) {
    SECURITY_ATTRIBUTES sa = {sizeof(sa), NULL, TRUE};
    HANDLE output = CreateFileW(logPath, FILE_APPEND_DATA, FILE_SHARE_READ|FILE_SHARE_WRITE, &sa, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    HANDLE input = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ|FILE_SHARE_WRITE, &sa, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    STARTUPINFOW si = {0}; si.cb = sizeof(si); si.dwFlags = STARTF_USESTDHANDLES|STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE; si.hStdInput = input; si.hStdOutput = output; si.hStdError = output;
    PROCESS_INFORMATION pi = {0};
    BOOL ok = CreateProcessW(NULL, command, NULL, NULL, TRUE, CREATE_NO_WINDOW|CREATE_SUSPENDED, NULL, source, &si, &pi);
    CloseHandle(input); CloseHandle(output);
    if(!ok) { logLine("Falha ao iniciar um componente do aplicativo."); return 1; }
    if(!AssignProcessToJobObject(processJob, pi.hProcess)) {
        TerminateProcess(pi.hProcess, 1); CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
        logLine("O Windows nao permitiu gerenciar os processos do aplicativo."); return 1;
    }
    ResumeThread(pi.hThread); CloseHandle(pi.hThread);
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code=1; GetExitCodeProcess(pi.hProcess, &code); CloseHandle(pi.hProcess);
    return code;
}
static BOOL download(const wchar_t *url, const wchar_t *target) {
    DeleteFileW(target);
    HRESULT hr = URLDownloadToFileW(NULL, url, target, 0, NULL);
    if(FAILED(hr)) {
        DeleteFileW(target);
        logLine("Nao foi possivel baixar os componentes. Verifique a internet e tente novamente.");
        return FALSE;
    }
    return TRUE;
}
static DWORD WINAPI worker(void *unused) {
    (void)unused;
    wchar_t path[CAP], cmd[CAP*3], folder[CAP], archive[CAP];
    DeleteFileW(readyPath);
    HANDLE f=CreateFileW(logPath, GENERIC_WRITE, FILE_SHARE_READ|FILE_SHARE_WRITE, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if(f!=INVALID_HANDLE_VALUE) CloseHandle(f);
    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    logLine("MidiDream - preparacao automatica\r\n");
    join(path, source, L"bootstrap.py");
    if(!exists(path)) { logLine("Extraia TODO o ZIP antes de abrir o programa. A pasta programa precisa ficar ao lado do EXE."); goto failed; }
    join(folder, base, L"gerenciador"); CreateDirectoryW(folder,NULL);
    join(uv, folder, L"uv.exe");
    if(!exists(uv)) {
        logLine("[1/5] Baixando o gerenciador de instalacao... (primeiro uso)");
        join(archive, base, L"gerenciador.zip");
        if(!download(L"https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip",archive)) goto failed;
        wchar_t system[CAP], tar[CAP]; GetSystemDirectoryW(system,CAP); join(tar,system,L"tar.exe");
        if(!exists(tar)) { logLine("Este aplicativo requer Windows 10 atualizado (1803 ou superior) ou Windows 11."); goto failed; }
        _snwprintf(cmd,CAP*3,L"\"%ls\" -xf \"%ls\" -C \"%ls\"",tar,archive,folder);
        if(execute(cmd)) goto failed;
        DeleteFileW(archive);
        if(!exists(uv)) { logLine("O pacote do gerenciador nao foi extraido corretamente."); goto failed; }
    }
    join(folder,base,L"python"); SetEnvironmentVariableW(L"UV_PYTHON_INSTALL_DIR",folder);
    join(folder,base,L"cache"); SetEnvironmentVariableW(L"UV_CACHE_DIR",folder);
    SetEnvironmentVariableW(L"UV_PYTHON_INSTALL_BIN",L"0");
    SetEnvironmentVariableW(L"UV_PYTHON_INSTALL_REGISTRY",L"0");
    SetEnvironmentVariableW(L"UV_NO_CONFIG",L"1");
    // --managed-python já seleciona o interpretador privado. O UV rejeita as duas opções juntas.
    SetEnvironmentVariableW(L"UV_PYTHON_PREFERENCE",NULL);
    SetEnvironmentVariableW(L"PYTHONUTF8",L"1");
    SetEnvironmentVariableW(L"PYTHONUNBUFFERED",L"1");
    SetEnvironmentVariableW(L"PYTHONIOENCODING",L"utf-8");
    join(folder,base,L"ambiente"); join(python,folder,L"Scripts\\python.exe");
    if(!exists(python)) {
        logLine("[2/5] Preparando Python automaticamente. Nao e necessario instalar nada manualmente.");
        _snwprintf(cmd,CAP*3,L"\"%ls\" venv --no-config --python 3.11 --managed-python \"%ls\"",uv,folder);
        if(execute(cmd)) goto failed;
    }
    if(!exists(python)) { logLine("O ambiente de processamento nao foi criado."); goto failed; }
    _snwprintf(cmd,CAP*3,L"\"%ls\" -u \"%ls\\bootstrap.py\" --base \"%ls\" --uv \"%ls\"",python,source,base,uv);
    if(execute(cmd)) goto failed;
    CoUninitialize(); PostMessageW(win,WM_FINISHED,0,0); return 0;
failed:
    logLine("\r\nA preparacao foi interrompida. O botao Tentar novamente retoma a instalacao.");
    CoUninitialize(); PostMessageW(win,WM_FINISHED,1,0); return 1;
}
static void updateLog(void) {
    HANDLE f=CreateFileW(logPath,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,NULL,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,NULL);
    if(f==INVALID_HANDLE_VALUE) return;
    LARGE_INTEGER size; GetFileSizeEx(f,&size);
    if(size.QuadPart>16000) { LARGE_INTEGER pos; pos.QuadPart=size.QuadPart-16000; SetFilePointerEx(f,pos,NULL,FILE_BEGIN); }
    char buf[16001]; DWORD n=0; ReadFile(f,buf,16000,&n,NULL);CloseHandle(f);buf[n]=0;
    wchar_t wide[16001]; MultiByteToWideChar(CP_UTF8,0,buf,-1,wide,16001);wide[16000]=0;
    int selectedStart=0,selectedEnd=0; SendMessageW(logText,EM_GETSEL,(WPARAM)&selectedStart,(LPARAM)&selectedEnd);
    SetWindowTextW(logText,wide); SendMessageW(logText,EM_SETSEL,-1,-1);SendMessageW(logText,EM_SCROLLCARET,0,0);
}
static void openInterface(void) {
    HANDLE f=CreateFileW(readyPath,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,NULL,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,NULL);
    if(f==INVALID_HANDLE_VALUE) return;
    char url[100]={0};DWORD n;ReadFile(f,url,99,&n,NULL);CloseHandle(f);url[n]=0;
    if(strncmp(url,"http://127.0.0.1:",17))return;
    wchar_t wide[100];MultiByteToWideChar(CP_UTF8,0,url,-1,wide,100);
    ShellExecuteW(win,L"open",wide,NULL,NULL,SW_SHOWNORMAL);
}
static HWND control(const wchar_t *cls,const wchar_t *text,DWORD style,int x,int y,int w,int h,int id) {
    HWND c=CreateWindowExW(0,cls,text,WS_CHILD|WS_VISIBLE|style,x,y,w,h,win,(HMENU)(INT_PTR)id,GetModuleHandleW(NULL),NULL);
    SendMessageW(c,WM_SETFONT,(WPARAM)bodyFont,TRUE);return c;
}
static LRESULT CALLBACK wndproc(HWND hwnd,UINT msg,WPARAM w,LPARAM l) {
    switch(msg) {
    case WM_CREATE: {
        win=hwnd;
        bodyFont=CreateFontW(-16,0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,CLEARTYPE_QUALITY,DEFAULT_PITCH,L"Segoe UI");
        titleFont=CreateFontW(-28,0,0,0,FW_SEMIBOLD,FALSE,FALSE,FALSE,DEFAULT_CHARSET,OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,CLEARTYPE_QUALITY,DEFAULT_PITCH,L"Segoe UI");
        HWND title=control(L"STATIC",L"MidiDream",0,24,18,650,42,0);SendMessageW(title,WM_SETFONT,(WPARAM)titleFont,TRUE);
        control(L"STATIC",L"Envie o MP3 na interface e gere seu MIDI. A preparação é automática.",0,25,65,655,26,0);
        statusText=control(L"STATIC",L"No primeiro uso, serão baixados componentes grandes. Mantenha a internet ativa.",0,25,103,650,42,0);
        progress=control(PROGRESS_CLASSW,L"",PBS_MARQUEE,25,154,650,13,0);
        startButton=control(L"BUTTON",L"Preparar e abrir",BS_DEFPUSHBUTTON|WS_TABSTOP,25,184,215,42,START_ID);
        control(L"BUTTON",L"Meus arquivos",WS_TABSTOP,255,184,190,42,FOLDER_ID);
        control(L"BUTTON",L"Ver diagnóstico",WS_TABSTOP,460,184,215,42,LOG_ID);
        logText=control(L"EDIT",L"Pronto para começar. Nenhum comando precisa ser digitado.",ES_MULTILINE|ES_READONLY|ES_AUTOVSCROLL|WS_VSCROLL|WS_BORDER,25,247,650,165,0);
        control(L"STATIC",L"Deixe esta janela aberta enquanto usa o programa. Seus resultados ficam salvos.",0,25,425,650,36,0);
        SetTimer(win,1,700,NULL);return 0; }
    case WM_COMMAND:
        if(LOWORD(w)==START_ID) {
            if(running && opened){openInterface();return 0;}
            if(InterlockedCompareExchange(&running,1,0))return 0;
            opened=FALSE;DeleteFileW(readyPath);EnableWindow(startButton,FALSE);
            SetWindowTextW(statusText,L"Preparando os componentes. Isso pode demorar no primeiro uso…");
            SendMessageW(progress,PBM_SETMARQUEE,TRUE,40);
            HANDLE thread=CreateThread(NULL,0,worker,NULL,0,NULL);
            if(thread)CloseHandle(thread);else{running=0;EnableWindow(startButton,TRUE);}
        } else if(LOWORD(w)==FOLDER_ID) {
            wchar_t path[CAP];join(path,base,L"trabalhos");CreateDirectoryW(path,NULL);
            ShellExecuteW(win,L"open",path,NULL,NULL,SW_SHOWNORMAL);
        } else if(LOWORD(w)==LOG_ID && exists(logPath))ShellExecuteW(win,L"open",L"notepad.exe",logPath,NULL,SW_SHOWNORMAL);
        return 0;
    case WM_TIMER:
        updateLog();
        if(running&&!opened&&exists(readyPath)) {
            opened=TRUE;SendMessageW(progress,PBM_SETMARQUEE,FALSE,0);
            SetWindowTextW(statusText,L"Aplicativo aberto! Use a interface que apareceu no navegador.");
            SetWindowTextW(startButton,L"Abrir interface");EnableWindow(startButton,TRUE);openInterface();
        }return 0;
    case WM_FINISHED:
        running=0;opened=FALSE;DeleteFileW(readyPath);EnableWindow(startButton,TRUE);
        SendMessageW(progress,PBM_SETMARQUEE,FALSE,0);updateLog();
        SetWindowTextW(startButton,w?L"Tentar novamente":L"Abrir novamente");
        SetWindowTextW(statusText,w?L"Não foi possível concluir. Veja o diagnóstico ou tente novamente.":L"O aplicativo foi encerrado. Seus arquivos continuam salvos.");return 0;
    case WM_CLOSE: DestroyWindow(hwnd);return 0;
    case WM_DESTROY:
        CloseHandle(processJob);DeleteFileW(readyPath);DeleteObject(bodyFont);DeleteObject(titleFont);PostQuitMessage(0);return 0;
    }return DefWindowProcW(hwnd,msg,w,l);
}
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE previous,LPWSTR args,int show) {
    (void)previous;(void)args;
    mutexHandle=CreateMutexW(NULL,TRUE,L"Local\\YouTubeMidiStudioDesktop");
    if(GetLastError()==ERROR_ALREADY_EXISTS) {
        HWND other=FindWindowW(L"YouTubeMidiStudioWindow",NULL);
        if(other){ShowWindow(other,SW_RESTORE);SetForegroundWindow(other);}return 0;
    }
    SetProcessDPIAware();
    GetModuleFileNameW(NULL,source,CAP);wchar_t *slash=wcsrchr(source,L'\\');if(slash)*slash=0;
    wchar_t folder[CAP];wcscpy(folder,source);join(source,folder,L"programa");
    if(FAILED(SHGetFolderPathW(NULL,CSIDL_LOCAL_APPDATA,NULL,SHGFP_TYPE_CURRENT,folder)))return 1;
    join(base,folder,L"YouTubeMidiStudio");CreateDirectoryW(base,NULL);
    join(logPath,base,L"diagnostico.txt");join(readyPath,base,L"interface.url.txt");
    DeleteFileW(readyPath);
    processJob=CreateJobObjectW(NULL,NULL);
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits={0};limits.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    SetInformationJobObject(processJob,JobObjectExtendedLimitInformation,&limits,sizeof(limits));
    INITCOMMONCONTROLSEX controls={sizeof(controls),ICC_PROGRESS_CLASS};InitCommonControlsEx(&controls);
    WNDCLASSW wc={0};wc.lpfnWndProc=wndproc;wc.hInstance=instance;wc.lpszClassName=L"YouTubeMidiStudioWindow";
    wc.hCursor=LoadCursor(NULL,IDC_ARROW);wc.hbrBackground=(HBRUSH)(COLOR_WINDOW+1);wc.hIcon=LoadIconW(instance,MAKEINTRESOURCEW(1));
    RegisterClassW(&wc);
    win=CreateWindowExW(0,wc.lpszClassName,L"MidiDream",WS_OVERLAPPED|WS_CAPTION|WS_SYSMENU|WS_MINIMIZEBOX,CW_USEDEFAULT,CW_USEDEFAULT,718,510,NULL,NULL,instance,NULL);
    ShowWindow(win,show);UpdateWindow(win);
    MSG message;while(GetMessageW(&message,NULL,0,0)>0){if(!IsDialogMessageW(win,&message)){TranslateMessage(&message);DispatchMessageW(&message);}}
    CloseHandle(mutexHandle);return 0;
}

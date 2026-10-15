; Inno Setup script for the Personalized Agent (section 99).
; Invoked by build/windows/build_windows.py with /D definitions.
;   Start Menu entry, application icon, user-level configuration, clean uninstall.
;   The last page asks whether the agent starts when the user signs in -
;   ticked by default. The program itself makes it so (--autostart on|off):
;   the same Run key and the same setting as Settings > General.
;   After where the program goes, WHERE THE DATA IS KEPT (workspace and
;   internal): written beside the program as data_root.txt; a folder that
;   already holds an agent's data is used only if the user says so.
;   THE FIRST PAGE asks which language - the nine the agent is shown in -
;   and the installer speaks it from then on; the program installed starts
;   in it (--display-language), changed later in Settings > Display.

#ifndef MyAppName
  #define MyAppName "Personalized Agent"
#endif
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "Personalized Agent Project"
#endif
#ifndef MyAppDirName
  #define MyAppDirName "PersonalizedAgent"
#endif
#ifndef MySourceDir
  #define MySourceDir "..\..\dist\windows\PersonalizedAgent"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\..\dist\windows"
#endif

#define MyAppExeName "PersonalizedAgent.exe"

[Setup]
AppId={{7C4E7B0E-2B4E-4E6B-9E52-5F1C9C7A1D21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppDirName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#MyOutputDir}
OutputBaseFilename=PersonalizedAgent-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Per-user install keeps configuration in the user profile and needs no elevation.
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=app.ico
; Which language, asked first - preselected as the system's own.
ShowLanguageDialog=yes

; Named by the agent's own codes: {language} is the code it is given.
[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "zh"; MessagesFile: "ChineseSimplified.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "ko"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"

[CustomMessages]
en.StartAtLogin=Start %1 when I sign in
zh.StartAtLogin=登录时启动 %1
es.StartAtLogin=Iniciar %1 al iniciar sesión
ja.StartAtLogin=サインイン時に %1 を起動する
ko.StartAtLogin=로그인할 때 %1 시작
ru.StartAtLogin=Запускать %1 при входе в систему
fr.StartAtLogin=Démarrer %1 à l'ouverture de session
de.StartAtLogin=%1 bei der Anmeldung starten
it.StartAtLogin=Avvia %1 all'accesso
en.Headless=%1 (no window)
zh.Headless=%1（无窗口）
es.Headless=%1 (sin ventana)
ja.Headless=%1（ウィンドウなし）
ko.Headless=%1 (창 없음)
ru.Headless=%1 (без окна)
fr.Headless=%1 (sans fenêtre)
de.Headless=%1 (ohne Fenster)
it.Headless=%1 (senza finestra)
en.DataTitle=Where to keep your data
zh.DataTitle=数据保存位置
es.DataTitle=Dónde guardar tus datos
ja.DataTitle=データの保存先
ko.DataTitle=데이터 저장 위치
ru.DataTitle=Где хранить данные
fr.DataTitle=Où conserver vos données
de.DataTitle=Speicherort Ihrer Daten
it.DataTitle=Dove conservare i dati
en.DataDescription=Everything %1 learns and makes is kept in one folder.
zh.DataDescription=%1 学到和产出的一切都保存在一个文件夹里。
es.DataDescription=Todo lo que %1 aprende y crea se guarda en una carpeta.
ja.DataDescription=%1 が学んだことと作ったものはすべて 1 つのフォルダーに保存されます。
ko.DataDescription=%1이(가) 배우고 만드는 모든 것은 한 폴더에 보관됩니다.
ru.DataDescription=Всё, что %1 узнаёт и создаёт, хранится в одной папке.
fr.DataDescription=Tout ce que %1 apprend et crée est conservé dans un seul dossier.
de.DataDescription=Alles, was %1 lernt und erstellt, liegt in einem Ordner.
it.DataDescription=Tutto ciò che %1 impara e crea è conservato in una cartella.
en.DataPrompt=Choose the folder for your data (workspace and internal). A different folder is a different agent, with its own calendar, contacts and keys. It cannot be changed later from the program. Choose an empty folder, or one that holds only an earlier Personal Agent's data.
zh.DataPrompt=选择保存数据的文件夹（workspace 和 internal）。不同的文件夹就是不同的助手，各有自己的日历、联系人和密钥。安装后不能在程序里更改。请选择空文件夹，或者只有之前 Personal Agent 数据的文件夹。
es.DataPrompt=Elige la carpeta para tus datos (workspace e internal). Otra carpeta es otro agente, con su propio calendario, contactos y claves. No se puede cambiar después desde el programa. Elige una carpeta vacía o una que solo contenga datos de un Personal Agent anterior.
ja.DataPrompt=データ（workspace と internal）を保存するフォルダーを選んでください。フォルダーが違えば別のエージェントになり、カレンダー・連絡先・キーもそれぞれ別です。あとからプログラム内では変更できません。空のフォルダー、または以前の Personal Agent のデータだけがあるフォルダーを選んでください。
ko.DataPrompt=데이터(workspace, internal)를 보관할 폴더를 선택하세요. 폴더가 다르면 다른 에이전트이며 캘린더, 연락처, 키도 따로입니다. 나중에 프로그램에서 바꿀 수 없습니다. 빈 폴더나 이전 Personal Agent의 데이터만 있는 폴더를 선택하세요.
ru.DataPrompt=Выберите папку для данных (workspace и internal). Другая папка — это другой агент со своим календарём, контактами и ключами. Позже в программе её изменить нельзя. Выберите пустую папку или папку только с данными прежнего Personal Agent.
fr.DataPrompt=Choisissez le dossier de vos données (workspace et internal). Un autre dossier est un autre agent, avec son propre calendrier, ses contacts et ses clés. Il ne peut pas être changé ensuite depuis le programme. Choisissez un dossier vide, ou qui ne contient que les données d'un Personal Agent précédent.
de.DataPrompt=Wählen Sie den Ordner für Ihre Daten (workspace und internal). Ein anderer Ordner ist ein anderer Agent mit eigenem Kalender, eigenen Kontakten und Schlüsseln. Er kann später im Programm nicht geändert werden. Wählen Sie einen leeren Ordner oder einen, der nur Daten eines früheren Personal Agent enthält.
it.DataPrompt=Scegli la cartella per i dati (workspace e internal). Una cartella diversa è un agente diverso, con calendario, contatti e chiavi propri. Non può essere cambiata dopo dal programma. Scegli una cartella vuota o che contenga solo i dati di un Personal Agent precedente.
en.DataExisting=This folder already has data of %1:%n%n%2%n%nUse the data that is already there?%n%nChoose No to pick another folder.
zh.DataExisting=这个文件夹里已经有 %1 的数据：%n%n%2%n%n要使用已有的数据吗？%n%n选“否”可以选择别的文件夹。
es.DataExisting=Esta carpeta ya tiene datos de %1:%n%n%2%n%n¿Usar los datos que ya están ahí?%n%nElige No para escoger otra carpeta.
ja.DataExisting=このフォルダーにはすでに %1 のデータがあります：%n%n%2%n%nこのデータを使いますか？%n%n別のフォルダーを選ぶには「いいえ」を選んでください。
ko.DataExisting=이 폴더에는 이미 %1의 데이터가 있습니다:%n%n%2%n%n기존 데이터를 사용할까요?%n%n다른 폴더를 고르려면 '아니요'를 선택하세요.
ru.DataExisting=В этой папке уже есть данные %1:%n%n%2%n%nИспользовать эти данные?%n%nВыберите «Нет», чтобы указать другую папку.
fr.DataExisting=Ce dossier contient déjà des données de %1:%n%n%2%n%nUtiliser les données qui s'y trouvent?%n%nChoisissez Non pour prendre un autre dossier.
de.DataExisting=Dieser Ordner enthält bereits Daten von %1:%n%n%2%n%nDie vorhandenen Daten verwenden?%n%nWählen Sie Nein, um einen anderen Ordner zu wählen.
it.DataExisting=Questa cartella contiene già dati di %1:%n%n%2%n%nUsare i dati già presenti?%n%nScegli No per indicare un'altra cartella.
en.DataProgramFiles=Choose a folder you can write to - not inside Program Files: it is not writable without administrator rights, and an upgrade would replace it.
zh.DataProgramFiles=请选择一个你可以写入的文件夹，不要放在 Program Files 里：那里没有管理员权限不能写入，升级时也会被替换。
es.DataProgramFiles=Elige una carpeta en la que puedas escribir, no dentro de Program Files: ahí no se puede escribir sin permisos de administrador y una actualización la reemplazaría.
ja.DataProgramFiles=書き込みできるフォルダーを選んでください。Program Files の中は不可です（管理者権限なしでは書き込めず、更新時に置き換えられます）。
ko.DataProgramFiles=쓸 수 있는 폴더를 선택하세요. Program Files 안은 안 됩니다. 관리자 권한 없이는 쓸 수 없고 업그레이드 때 교체됩니다.
ru.DataProgramFiles=Выберите папку, в которую можно писать, — не внутри Program Files: туда нельзя писать без прав администратора, и обновление её заменит.
fr.DataProgramFiles=Choisissez un dossier où vous pouvez écrire, pas dans Program Files: on ne peut pas y écrire sans droits d'administrateur, et une mise à jour le remplacerait.
de.DataProgramFiles=Wählen Sie einen Ordner, in den Sie schreiben können - nicht in Program Files: Dort kann ohne Administratorrechte nicht geschrieben werden, und ein Update würde ihn ersetzen.
it.DataProgramFiles=Scegli una cartella in cui puoi scrivere, non dentro Program Files: lì non si può scrivere senza diritti di amministratore e un aggiornamento la sostituirebbe.
en.DataEmpty=Choose a folder for your data.
en.DataNotOnly=%1 already holds other things (%2).%n%nThe data folder must be empty, or hold only an earlier %3's data (workspace and internal): everything in it belongs to the agent and is never listened to, so work kept there would go unseen.%n%nChoose an empty folder.
zh.DataEmpty=请选择保存数据的文件夹。
zh.DataNotOnly=%1 里已经有别的东西（%2）。%n%n数据文件夹必须是空的，或者只有之前 %3 的数据（workspace 和 internal）：里面的一切都属于助手，而且永远不会被监听，放在那里的工作就看不到了。%n%n请选择一个空文件夹。
es.DataEmpty=Elige una carpeta para tus datos.
es.DataNotOnly=%1 ya contiene otras cosas (%2).%n%nLa carpeta de datos debe estar vacía o contener solo datos de un %3 anterior (workspace e internal): todo lo que hay en ella pertenece al agente y nunca se escucha, así que el trabajo guardado ahí pasaría desapercibido.%n%nElige una carpeta vacía.
ja.DataEmpty=データの保存先フォルダーを選んでください。
ja.DataNotOnly=%1 にはすでに別のもの（%2）があります。%n%nデータフォルダーは空か、以前の %3 のデータ（workspace と internal）だけを含む必要があります。中身はすべてエージェントのものとして扱われ、監視されないため、そこに置いた作業は記録されません。%n%n空のフォルダーを選んでください。
ko.DataEmpty=데이터를 보관할 폴더를 선택하세요.
ko.DataNotOnly=%1에는 이미 다른 것이 있습니다(%2).%n%n데이터 폴더는 비어 있거나 이전 %3의 데이터(workspace, internal)만 있어야 합니다. 그 안의 모든 것은 에이전트의 것으로 보고 감지하지 않으므로, 거기 둔 작업은 보이지 않게 됩니다.%n%n빈 폴더를 선택하세요.
ru.DataEmpty=Выберите папку для данных.
ru.DataNotOnly=В %1 уже есть другое (%2).%n%nПапка данных должна быть пустой или содержать только данные прежнего %3 (workspace и internal): всё в ней принадлежит агенту и не отслеживается, поэтому работа, хранящаяся там, останется незамеченной.%n%nВыберите пустую папку.
fr.DataEmpty=Choisissez un dossier pour vos données.
fr.DataNotOnly=%1 contient déjà d'autres éléments (%2).%n%nLe dossier de données doit être vide, ou ne contenir que les données d'un %3 précédent (workspace et internal): tout ce qu'il contient appartient à l'agent et n'est jamais écouté, donc le travail qui s'y trouve passerait inaperçu.%n%nChoisissez un dossier vide.
de.DataEmpty=Wählen Sie einen Ordner für Ihre Daten.
de.DataNotOnly=%1 enthält bereits andere Dinge (%2).%n%nDer Datenordner muss leer sein oder nur Daten eines früheren %3 enthalten (workspace und internal): Alles darin gehört dem Agenten und wird nie beobachtet, sodass dort abgelegte Arbeit unbemerkt bliebe.%n%nWählen Sie einen leeren Ordner.
it.DataEmpty=Scegli una cartella per i dati.
it.DataNotOnly=%1 contiene già altre cose (%2).%n%nLa cartella dei dati deve essere vuota o contenere solo i dati di un %3 precedente (workspace e internal): tutto ciò che contiene appartiene all'agente e non viene mai osservato, quindi il lavoro tenuto lì passerebbe inosservato.%n%nScegli una cartella vuota.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:Headless,{#MyAppName}}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--headless"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; THE LAST PAGE: ticked by default, and done in a silent install too. As the
; user who ran the installer - the Run key is theirs, not an administrator's.
; FIRST, ALWAYS: the program shown in the language the installer was run in.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--display-language {language}"; Flags: runhidden waituntilterminated runasoriginaluser
Filename: "{app}\{#MyAppExeName}"; Parameters: "--autostart on"; Description: "{cm:StartAtLogin,{#MyAppName}}"; Flags: postinstall runhidden waituntilterminated runasoriginaluser
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
; Not started at sign-in once it is gone.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--autostart off"; Flags: runhidden waituntilterminated; RunOnceId: "AutostartOff"
; And Explorer's "Delegate work" taken out of its right-click menu.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--explorer-menu off"; Flags: runhidden waituntilterminated; RunOnceId: "ExplorerMenuOff"

[Code]
// WHERE THE DATA IS KEPT, asked after where the program goes: one folder for
// workspace\ and internal\, written beside the program (data_root.txt) and
// read by it before anything else (common/paths.py, installed_root). A
// different folder is a different agent: its own calendar, contacts, keys.
// An upgrade shows the folder chosen before. A folder that already holds an
// agent's data is used only if the user says so; No keeps them on the page.
var
  DataPage: TInputDirWizardPage;

function HasAgentData(Dir: String): Boolean;
begin
  Result := FileExists(AddBackslash(Dir) + 'internal\database\agent.db') or
            FileExists(AddBackslash(Dir) + 'internal\settings\settings.json');
end;

// What a data folder holds besides an agent's own two folders (and the files
// the system puts in any folder): the first few names, or '' when nothing.
function OtherThings(Dir: String): String;
var
  FindRec: TFindRec;
  Name: String;
  Count: Integer;
begin
  Result := '';
  Count := 0;
  if FindFirst(AddBackslash(Dir) + '*', FindRec) then
  try
    repeat
      Name := Lowercase(FindRec.Name);
      if (Name <> '.') and (Name <> '..') and (Name <> 'workspace') and
         (Name <> 'internal') and (Name <> 'desktop.ini') and (Name <> 'thumbs.db') then begin
        if Count < 3 then begin
          if Result <> '' then Result := Result + ', ';
          Result := Result + FindRec.Name;
        end;
        Count := Count + 1;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
  if Count > 3 then Result := Result + ', ...';
end;

function InsideProgramFiles(Dir: String): Boolean;
var
  Folder: String;
begin
  Folder := Lowercase(AddBackslash(ExpandFileName(Dir)));
  Result := (Pos(Lowercase(AddBackslash(ExpandConstant('{commonpf}'))), Folder) = 1) or
            (Pos(Lowercase(AddBackslash(ExpandConstant('{commonpf32}'))), Folder) = 1);
end;

procedure InitializeWizard;
begin
  DataPage := CreateInputDirPage(wpSelectDir, CustomMessage('DataTitle'),
    FmtMessage(CustomMessage('DataDescription'), ['{#MyAppName}']),
    CustomMessage('DataPrompt'), False, '');
  DataPage.Add('');
  DataPage.Values[0] := GetPreviousData('DataFolder',
    ExpandConstant('{%USERPROFILE}\{#MyAppDirName}'));
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'DataFolder', DataPage.Values[0]);
end;

function CheckDataFolder: Boolean;
var
  Dir: String;
begin
  Result := True;
  Dir := Trim(DataPage.Values[0]);
  if Dir = '' then begin
    MsgBox(CustomMessage('DataEmpty'), mbError, MB_OK);
    Result := False;
  end else if InsideProgramFiles(Dir) then begin
    MsgBox(CustomMessage('DataProgramFiles'), mbError, MB_OK);
    Result := False;
  end else if OtherThings(Dir) <> '' then begin
    // Everything in it is the agent's and never listened to: a folder with
    // other things in it would hide the work kept there.
    MsgBox(FmtMessage(CustomMessage('DataNotOnly'), [Dir, OtherThings(Dir), '{#MyAppName}']),
           mbError, MB_OK);
    Result := False;
  end else if HasAgentData(Dir) and (not WizardSilent) then
    Result := MsgBox(FmtMessage(CustomMessage('DataExisting'), ['{#MyAppName}', Dir]),
                     mbConfirmation, MB_YESNO) = IDYES;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // Before the [Run] entries: the program's first start already reads it.
  if CurStep = ssPostInstall then
    SaveStringToFile(ExpandConstant('{app}\data_root.txt'),
                     ExpandFileName(Trim(DataPage.Values[0])), False);
end;

// Left unticked on the last page: said so, so the program's own default
// (on) does not turn it back on at its first start.
function NextButtonClick(CurPageID: Integer): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if CurPageID = DataPage.ID then
    Result := CheckDataFolder;
  if (CurPageID = wpFinished) and (not WizardSilent) and
     (WizardForm.RunList.Items.Count > 0) and (not WizardForm.RunList.Checked[0]) then
    ExecAsOriginalUser(ExpandConstant('{app}\{#MyAppExeName}'), '--autostart off', '',
                       SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

[UninstallDelete]
; Application files only. User settings, the knowledge graph and the workspace
; are left in place; removing them is an explicit user action.
Type: filesandordirs; Name: "{app}"

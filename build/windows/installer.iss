; Inno Setup script for Personal Agent WaterMelon (section 99).
; Invoked by build/windows/build_windows.py with /D definitions.
;   Start Menu entry, application icon, user-level configuration, clean uninstall.
;   The last page asks whether the agent starts when the user signs in -
;   ticked by default. The program itself makes it so (--autostart on|off):
;   the same Run key and the same setting as Settings > General.
;   After where the program goes, WHERE THE DATA IS KEPT (workspace and
;   internal): written beside the program as data_root.txt; a folder that
;   already holds an agent's data is used only if the user says so. Every
;   folder found holding one is listed there to choose from, with when it was
;   last used and its size; the box starts at the folder this copy used.
;   THE FIRST PAGE asks which language - the nine the agent is shown in -
;   and the installer speaks it from then on; the program installed starts
;   in it (--display-language), changed later in Settings > Display.

#ifndef MyAppName
  #define MyAppName "Personal Agent WaterMelon"
#endif
#ifndef MyAppVersion
  #define MyAppVersion "0.2.0"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "Personal Agent WaterMelon Project"
#endif
#ifndef MyAppDirName
  #define MyAppDirName "PersonalAgentWaterMelon"
#endif
#ifndef MySourceDir
  #define MySourceDir "..\..\dist\windows\PersonalAgentWaterMelon"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\..\dist\windows"
#endif

#define MyAppExeName "PersonalAgentWaterMelon.exe"
; The program as its file was named before (to 1.0.0): removed on an update,
; and its sign-in entry taken as this program's.
#define MyFormerExeName "PersonalizedAgent.exe"

[Setup]
AppId={{7C4E7B0E-2B4E-4E6B-9E52-5F1C9C7A1D21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppDirName}
DefaultGroupName={#MyAppName}
; Renamed from Personalized Agent (1.0.0): the Start menu folder is the new
; name's, not the one an upgrade would otherwise keep.
UsePreviousGroup=no
DisableProgramGroupPage=yes
OutputDir={#MyOutputDir}
OutputBaseFilename=PersonalAgentWaterMelon-Setup
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
en.AutoUpdate=Install updates by themselves
zh.AutoUpdate=自动安装更新
es.AutoUpdate=Instalar las actualizaciones automáticamente
ja.AutoUpdate=アップデートを自動でインストールする
ko.AutoUpdate=업데이트 자동 설치
ru.AutoUpdate=Устанавливать обновления автоматически
fr.AutoUpdate=Installer les mises à jour automatiquement
de.AutoUpdate=Updates automatisch installieren
it.AutoUpdate=Installa gli aggiornamenti automaticamente
en.UpdatesGroup=Updates:
zh.UpdatesGroup=更新：
es.UpdatesGroup=Actualizaciones:
ja.UpdatesGroup=アップデート:
ko.UpdatesGroup=업데이트:
ru.UpdatesGroup=Обновления:
fr.UpdatesGroup=Mises à jour :
de.UpdatesGroup=Updates:
it.UpdatesGroup=Aggiornamenti:
en.StartAtLogin=Start %1 when I sign in
zh.StartAtLogin=登录时启动 %1
es.StartAtLogin=Iniciar %1 al iniciar sesión
ja.StartAtLogin=サインイン時に %1 を起動する
ko.StartAtLogin=로그인할 때 %1 시작
ru.StartAtLogin=Запускать %1 при входе в систему
fr.StartAtLogin=Démarrer %1 à l'ouverture de session
de.StartAtLogin=%1 bei der Anmeldung starten
it.StartAtLogin=Avvia %1 all'accesso
en.StartAtLoginTaken=Another copy of %1 already starts when you sign in:%n%2%n%nUntick "Start %1 when I sign in" to finish, or turn it off in that copy first.
zh.StartAtLoginTaken=已经有另一个 %1 设置了登录时启动：%n%2%n%n请取消勾选“登录时启动 %1”后再点完成，或者先在那个程序里关掉它。
es.StartAtLoginTaken=Otra copia de %1 ya se inicia al iniciar sesión:%n%2%n%nDesmarca «Iniciar %1 al iniciar sesión» para terminar, o desactívalo primero en esa copia.
ja.StartAtLoginTaken=別の %1 がすでにサインイン時に起動するよう設定されています：%n%2%n%n完了するには「サインイン時に %1 を起動する」のチェックを外すか、先にそちらで無効にしてください。
ko.StartAtLoginTaken=다른 %1이(가) 이미 로그인할 때 시작되도록 설정되어 있습니다:%n%2%n%n마치려면 "로그인할 때 %1 시작" 선택을 해제하거나, 먼저 그쪽에서 끄세요.
ru.StartAtLoginTaken=Другая копия %1 уже запускается при входе в систему:%n%2%n%nСнимите флажок «Запускать %1 при входе в систему», чтобы завершить, или сначала отключите его в той копии.
fr.StartAtLoginTaken=Une autre copie de %1 démarre déjà à l'ouverture de session :%n%2%n%nDécochez « Démarrer %1 à l'ouverture de session » pour terminer, ou désactivez-le d'abord dans cette copie.
de.StartAtLoginTaken=Eine andere Kopie von %1 startet bereits bei der Anmeldung:%n%2%n%nEntfernen Sie das Häkchen bei „%1 bei der Anmeldung starten“, um abzuschließen, oder schalten Sie es zuerst in jener Kopie aus.
it.StartAtLoginTaken=Un'altra copia di %1 si avvia già all'accesso:%n%2%n%nTogli la spunta da «Avvia %1 all'accesso» per finire, oppure disattivalo prima in quella copia.
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
en.DataPrompt=Choose the folder for your data (workspace and internal). A different folder is a different agent, with its own calendar, contacts and keys. It cannot be changed later from the program. Choose an empty folder, or one that holds only an earlier Personal Agent WaterMelon's data.
zh.DataPrompt=选择保存数据的文件夹（workspace 和 internal）。不同的文件夹就是不同的助手，各有自己的日历、联系人和密钥。安装后不能在程序里更改。请选择空文件夹，或者只有之前 Personal Agent WaterMelon 数据的文件夹。
es.DataPrompt=Elige la carpeta para tus datos (workspace e internal). Otra carpeta es otro agente, con su propio calendario, contactos y claves. No se puede cambiar después desde el programa. Elige una carpeta vacía o una que solo contenga datos de un Personal Agent WaterMelon anterior.
ja.DataPrompt=データ（workspace と internal）を保存するフォルダーを選んでください。フォルダーが違えば別のエージェントになり、カレンダー・連絡先・キーもそれぞれ別です。あとからプログラム内では変更できません。空のフォルダー、または以前の Personal Agent WaterMelon のデータだけがあるフォルダーを選んでください。
ko.DataPrompt=데이터(workspace, internal)를 보관할 폴더를 선택하세요. 폴더가 다르면 다른 에이전트이며 캘린더, 연락처, 키도 따로입니다. 나중에 프로그램에서 바꿀 수 없습니다. 빈 폴더나 이전 Personal Agent WaterMelon의 데이터만 있는 폴더를 선택하세요.
ru.DataPrompt=Выберите папку для данных (workspace и internal). Другая папка — это другой агент со своим календарём, контактами и ключами. Позже в программе её изменить нельзя. Выберите пустую папку или папку только с данными прежнего Personal Agent WaterMelon.
fr.DataPrompt=Choisissez le dossier de vos données (workspace et internal). Un autre dossier est un autre agent, avec son propre calendrier, ses contacts et ses clés. Il ne peut pas être changé ensuite depuis le programme. Choisissez un dossier vide, ou qui ne contient que les données d'un Personal Agent WaterMelon précédent.
de.DataPrompt=Wählen Sie den Ordner für Ihre Daten (workspace und internal). Ein anderer Ordner ist ein anderer Agent mit eigenem Kalender, eigenen Kontakten und Schlüsseln. Er kann später im Programm nicht geändert werden. Wählen Sie einen leeren Ordner oder einen, der nur Daten eines früheren Personal Agent WaterMelon enthält.
it.DataPrompt=Scegli la cartella per i dati (workspace e internal). Una cartella diversa è un agente diverso, con calendario, contatti e chiavi propri. Non può essere cambiata dopo dal programma. Scegli una cartella vuota o che contenga solo i dati di un Personal Agent WaterMelon precedente.
en.DataExisting=This folder already has data of %1:%n%n%2%n%nUse the data that is already there?%n%nChoose No to pick another folder.
zh.DataExisting=这个文件夹里已经有 %1 的数据：%n%n%2%n%n要使用已有的数据吗？%n%n选“否”可以选择别的文件夹。
es.DataExisting=Esta carpeta ya tiene datos de %1:%n%n%2%n%n¿Usar los datos que ya están ahí?%n%nElige No para escoger otra carpeta.
ja.DataExisting=このフォルダーにはすでに %1 のデータがあります：%n%n%2%n%nこのデータを使いますか？%n%n別のフォルダーを選ぶには「いいえ」を選んでください。
ko.DataExisting=이 폴더에는 이미 %1의 데이터가 있습니다:%n%n%2%n%n기존 데이터를 사용할까요?%n%n다른 폴더를 고르려면 '아니요'를 선택하세요.
ru.DataExisting=В этой папке уже есть данные %1:%n%n%2%n%nИспользовать эти данные?%n%nВыберите «Нет», чтобы указать другую папку.
fr.DataExisting=Ce dossier contient déjà des données de %1:%n%n%2%n%nUtiliser les données qui s'y trouvent?%n%nChoisissez Non pour prendre un autre dossier.
de.DataExisting=Dieser Ordner enthält bereits Daten von %1:%n%n%2%n%nDie vorhandenen Daten verwenden?%n%nWählen Sie Nein, um einen anderen Ordner zu wählen.
it.DataExisting=Questa cartella contiene già dati di %1:%n%n%2%n%nUsare i dati già presenti?%n%nScegli No per indicare un'altra cartella.
en.DataProgramFiles=Choose a folder you can write to. This one needs administrator rights, and %1 works as you, without them.
zh.DataProgramFiles=请选择一个你可以写入的文件夹。这个文件夹需要管理员权限才能写入，而 %1 以你的身份运行，没有这个权限。
es.DataProgramFiles=Elige una carpeta en la que puedas escribir. Esta requiere permisos de administrador, y %1 funciona como tú, sin ellos.
ja.DataProgramFiles=書き込みできるフォルダーを選んでください。このフォルダーへの書き込みには管理者権限が必要ですが、%1 は管理者権限なしであなたとして動作します。
ko.DataProgramFiles=쓸 수 있는 폴더를 선택하세요. 이 폴더는 관리자 권한이 있어야 쓸 수 있는데, %1은(는) 관리자 권한 없이 사용자 권한으로 실행됩니다.
ru.DataProgramFiles=Выберите папку, в которую можно писать. Для этой нужны права администратора, а %1 работает от вашего имени, без них.
fr.DataProgramFiles=Choisissez un dossier où vous pouvez écrire. Celui-ci exige des droits d'administrateur, et %1 fonctionne en votre nom, sans eux.
de.DataProgramFiles=Wählen Sie einen Ordner, in den Sie schreiben können. Für diesen sind Administratorrechte nötig, und %1 läuft als Sie, ohne diese Rechte.
it.DataProgramFiles=Scegli una cartella in cui puoi scrivere. Questa richiede diritti di amministratore, e %1 funziona come te, senza di essi.
en.DataFound=These folders already hold data of %1 (last used, size). Choose one to use it again:
zh.DataFound=以下文件夹里已经有 %1 的数据（最后使用时间、大小）。选择一个即可继续使用：
es.DataFound=Estas carpetas ya tienen datos de %1 (último uso, tamaño). Elige una para volver a usarla:
ja.DataFound=次のフォルダーにはすでに %1 のデータがあります（最終使用日時、サイズ）。もう一度使うフォルダーを選んでください：
ko.DataFound=다음 폴더에 이미 %1의 데이터가 있습니다(마지막 사용, 크기). 다시 사용할 폴더를 선택하세요:
ru.DataFound=В этих папках уже есть данные %1 (последнее использование, размер). Выберите одну, чтобы снова её использовать:
fr.DataFound=Ces dossiers contiennent déjà des données de %1 (dernière utilisation, taille). Choisissez-en un pour le réutiliser :
de.DataFound=Diese Ordner enthalten bereits Daten von %1 (zuletzt verwendet, Größe). Wählen Sie einen, um ihn weiter zu verwenden:
it.DataFound=Queste cartelle contengono già dati di %1 (ultimo uso, dimensione). Scegline una per usarla di nuovo:
en.DataHoldsProgram=The data folder cannot be the program's own folder, or a folder the program is installed in:%n%n%1%n%nChoose another folder, for example a "data" folder inside the program's folder.
zh.DataHoldsProgram=数据文件夹不能是程序本身的文件夹，也不能是包含程序的文件夹：%n%n%1%n%n请选择别的文件夹，例如程序文件夹里的 "data" 文件夹。
es.DataHoldsProgram=La carpeta de datos no puede ser la carpeta del programa ni una carpeta que lo contenga:%n%n%1%n%nElige otra carpeta, por ejemplo una carpeta "data" dentro de la del programa.
ja.DataHoldsProgram=データフォルダーには、プログラム自体のフォルダーや、プログラムが入っているフォルダーは使えません：%n%n%1%n%n別のフォルダー（たとえばプログラムのフォルダー内の "data" フォルダー）を選んでください。
ko.DataHoldsProgram=데이터 폴더는 프로그램 자체의 폴더나 프로그램이 설치된 폴더를 포함하는 폴더일 수 없습니다:%n%n%1%n%n다른 폴더를 선택하세요. 예: 프로그램 폴더 안의 "data" 폴더.
ru.DataHoldsProgram=Папка данных не может быть папкой самой программы или папкой, в которой программа установлена:%n%n%1%n%nВыберите другую папку, например папку "data" внутри папки программы.
fr.DataHoldsProgram=Le dossier de données ne peut pas être le dossier du programme, ni un dossier qui le contient :%n%n%1%n%nChoisissez un autre dossier, par exemple un dossier "data" dans celui du programme.
de.DataHoldsProgram=Der Datenordner darf weder der Ordner des Programms sein noch ein Ordner, der es enthält:%n%n%1%n%nWählen Sie einen anderen Ordner, zum Beispiel einen Ordner "data" im Ordner des Programms.
it.DataHoldsProgram=La cartella dei dati non può essere la cartella del programma né una cartella che lo contiene:%n%n%1%n%nScegli un'altra cartella, per esempio una cartella "data" dentro quella del programma.
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
; UPDATES are had by themselves only if this is ticked; otherwise the user is
; told and chooses the version (platform_layer/updater.py) - unticked by
; default (the user, 2026-10-27). Either way an update is a quiet one: made
; ready in the background, used from the next start, taken back by itself if
; it does not start (platform_layer/quiet_update.py). A reinstall remembers
; the box as it was left.
Name: "autoupdate"; Description: "{cm:AutoUpdate}"; GroupDescription: "{cm:UpdatesGroup}"; Flags: unchecked

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; What 1.0.0 put on the Start menu and the desktop under its old name -
; and its "(no window)" shortcut, which is gone.
Type: filesandordirs; Name: "{autoprograms}\Personalized Agent"
Type: files; Name: "{autodesktop}\Personalized Agent.lnk"
Type: files; Name: "{app}\{#MyFormerExeName}"
; A QUIET UPDATE (platform_layer/quiet_update.py) changes the program's own
; files and keeps the version it replaced beside them: a full install starts
; from none of that.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\.update"

[UninstallDelete]
; What a quiet update put there, which this installer's record does not list.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\.update"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; THE LAST PAGE: ticked by default, and done in a silent install too - but
; not in an UPDATE, a silent install over a copy already here, which leaves
; the user's own choice as it is. As the user who ran the installer - the Run
; key is theirs, not an administrator's.
; FIRST, ALWAYS: the program shown in the language the installer was run in.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--display-language {language}"; Flags: runhidden waituntilterminated runasoriginaluser
; How updates are taken, as the box on the tasks page was left - not in an
; update, which keeps the user's own choice.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--updates install"; Flags: runhidden waituntilterminated runasoriginaluser; Tasks: autoupdate; Check: not AnUpdate
Filename: "{app}\{#MyAppExeName}"; Parameters: "--updates ask"; Flags: runhidden waituntilterminated runasoriginaluser; Tasks: not autoupdate; Check: not AnUpdate
Filename: "{app}\{#MyAppExeName}"; Parameters: "--autostart on"; Description: "{cm:StartAtLogin,{#MyAppName}}"; Flags: postinstall runhidden waituntilterminated runasoriginaluser; Check: not AnUpdate
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser
; AN UPDATE (platform_layer/updater.py) runs this silently once the program
; has quit: it is started again, on the new version.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait runasoriginaluser; Check: AnUpdate

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
  // The folder chosen before (an upgrade), or '' on a first install.
  PreviousData: String;
  // The default last put in the box - followed while the user has not typed.
  DataDefault: String;
  // Said under the box while the folder needs an administrator.
  DataNeedsAdmin: TNewStaticText;
  // The folders found holding an agent's data, listed under the box to
  // choose from - and the program folder they were looked for from.
  DataFoundTitle: TNewStaticText;
  DataFound: TNewCheckListBox;
  FoundFolders: TStringList;
  FoundFor: String;
  // The data folder of the program being uninstalled, read before its files go.
  UninstallDataRoot: String;

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

// Whether `Folder` is `Base` or inside it.
function Under(Folder, Base: String): Boolean;
begin
  Result := Pos(Lowercase(AddBackslash(Base)), Lowercase(AddBackslash(Folder))) = 1;
end;

// THE FOLDERS THAT ALREADY HOLD AN AGENT'S DATA, OFFERED TO CHOOSE FROM (the
// user, 2026-10-29). A copy installed anew suggested <program>\data while the
// data its user had kept for months was in their own folder, and showed an
// empty agent. Nothing is chosen for the user but the folder this same copy
// used: one computer can hold the agent the user lives with and a copy
// installed to test, and which one is meant is theirs to say - so every
// folder found is listed, with when it was last used and how big it is.
type
  TClockTime = record
    Year, Month, DayOfWeek, Day, Hour, Minute, Second, Milliseconds: Word;
  end;

const
  REPARSE_POINT = $400;
  // How long one folder's size is counted for, in milliseconds.
  SIZE_COUNTED_FOR = 1500;

function FileTimeToLocalFileTime(var Utc: TFileTime; var Local: TFileTime): Boolean;
  external 'FileTimeToLocalFileTime@kernel32.dll stdcall';
function FileTimeToSystemTime(var Time: TFileTime; var Clock: TClockTime): Boolean;
  external 'FileTimeToSystemTime@kernel32.dll stdcall';
function TicksNow: DWORD;
  external 'GetTickCount@kernel32.dll stdcall';

function FolderOf(Dir: String): String;
begin
  Result := RemoveBackslash(ExpandFileName(Trim(Dir)));
end;

function SameFolder(A, B: String): Boolean;
begin
  Result := (Trim(A) <> '') and (Trim(B) <> '') and
            (CompareText(FolderOf(A), FolderOf(B)) = 0);
end;

// The folder the copy in `AppDir` reads its data from (data_root.txt) - ''
// when there is none.
function InstalledData(AppDir: String): String;
var
  Raw: AnsiString;
begin
  Result := '';
  if (Trim(AppDir) <> '') and
     LoadStringFromFile(AddBackslash(AppDir) + 'data_root.txt', Raw) then
    Result := Trim(String(Raw));
end;

// Where a copy from before the data folder was asked (0.2.0) kept its data:
// in the user's own folder, as the program finds it (common/paths.py,
// default_root) - under its name now, else under its name before.
function StandardData: String;
var
  Home: String;
begin
  Home := AddBackslash(GetEnv('USERPROFILE'));
  Result := Home + '{#MyAppDirName}';
  if (not DirExists(Result)) and DirExists(Home + 'PersonalizedAgent') then
    Result := Home + 'PersonalizedAgent';
end;

// THE FOLDER THIS COPY USED: the one the copy in `AppDir` reads, else the one
// chosen when it was last installed, else - over a copy from before either
// was written - the standard one it used. '' for a copy installed anew.
function CopysData(AppDir: String): String;
begin
  Result := InstalledData(AppDir);
  if Result = '' then Result := PreviousData;
  if (Result = '') and (FileExists(AddBackslash(AppDir) + '{#MyAppExeName}') or
                        FileExists(AddBackslash(AppDir) + '{#MyFormerExeName}')) then
    Result := StandardData;
end;

// How many bytes `Dir` holds, counted until the clock reaches `Deadline`;
// `Cut` says it was not counted to the end.
function BytesIn(Dir: String; Deadline: DWORD; var Cut: Boolean): Int64;
var
  FindRec: TFindRec;
begin
  Result := 0;
  if FindFirst(AddBackslash(Dir) + '*', FindRec) then
  try
    repeat
      if TicksNow > Deadline then
        Cut := True
      else if (FindRec.Name <> '.') and (FindRec.Name <> '..') then begin
        if FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then begin
          if FindRec.Attributes and REPARSE_POINT = 0 then
            Result := Result + BytesIn(AddBackslash(Dir) + FindRec.Name, Deadline, Cut);
        end else
          Result := Result + Int64(FindRec.SizeHigh) * 4294967296 + Int64(FindRec.SizeLow);
      end;
    until Cut or (not FindNext(FindRec));
  finally
    FindClose(FindRec);
  end;
end;

function SizeText(Bytes: Int64; Cut: Boolean): String;
var
  Tenths: Int64;
begin
  if Bytes >= 1073741824 then begin
    Tenths := (Bytes * 10) div 1073741824;
    Result := IntToStr(Tenths div 10) + '.' + IntToStr(Tenths mod 10) + ' GB';
  end else if Bytes >= 1048576 then
    Result := IntToStr(Bytes div 1048576) + ' MB'
  else
    Result := IntToStr(Bytes div 1024) + ' KB';
  if Cut then Result := '> ' + Result;
end;

function TwoDigits(N: Integer): String;
begin
  Result := IntToStr(N);
  if N < 10 then Result := '0' + Result;
end;

// `Path`'s last write, kept in `Latest` when it is later than what is there.
procedure TakeLater(Path: String; var Latest: TFileTime; var Found: Boolean);
var
  FindRec: TFindRec;
begin
  if FindFirst(Path, FindRec) then
  try
    if (not Found) or
       (FindRec.LastWriteTime.dwHighDateTime > Latest.dwHighDateTime) or
       ((FindRec.LastWriteTime.dwHighDateTime = Latest.dwHighDateTime) and
        (FindRec.LastWriteTime.dwLowDateTime > Latest.dwLowDateTime)) then begin
      Latest := FindRec.LastWriteTime;
      Found := True;
    end;
  finally
    FindClose(FindRec);
  end;
end;

// When the agent last used `Dir`: the last its database, settings or log was
// written, in this computer's time - '' when none of them is there.
function LastUsed(Dir: String): String;
var
  Latest, Local: TFileTime;
  Clock: TClockTime;
  Found: Boolean;
  Internal: String;
begin
  Result := '';
  Found := False;
  Internal := AddBackslash(Dir) + 'internal\';
  TakeLater(Internal + 'database\agent.db', Latest, Found);
  TakeLater(Internal + 'database\agent.db-wal', Latest, Found);
  TakeLater(Internal + 'settings\settings.json', Latest, Found);
  TakeLater(Internal + 'logs\agent.log', Latest, Found);
  if Found and FileTimeToLocalFileTime(Latest, Local) and
     FileTimeToSystemTime(Local, Clock) then
    Result := IntToStr(Clock.Year) + '-' + TwoDigits(Clock.Month) + '-' +
              TwoDigits(Clock.Day) + ' ' + TwoDigits(Clock.Hour) + ':' +
              TwoDigits(Clock.Minute);
end;

// `Dir` in the list, when it holds an agent's data and is not there yet.
procedure Offer(Dir: String);
var
  K: Integer;
begin
  if (Trim(Dir) = '') or (not HasAgentData(Trim(Dir))) then Exit;
  for K := 0 to FoundFolders.Count - 1 do
    if SameFolder(FoundFolders[K], Dir) then Exit;
  FoundFolders.Add(FolderOf(Dir));
end;

// Every folder known to hold an agent's data: the one this copy used, the
// one chosen at the last install, those the copies in the usual places read,
// the standard ones in the user's folder, and those beside the programs. A
// folder only ever given with --root is not known here; it can be typed.
procedure FindFolders(AppDir: String);
var
  Home, Shown: String;
  K: Integer;
  Cut: Boolean;
  Bytes: Int64;
begin
  FoundFolders.Clear;
  Offer(CopysData(AppDir));
  Offer(PreviousData);
  Offer(InstalledData(ExpandConstant('{userpf}\{#MyAppDirName}')));
  Offer(InstalledData(ExpandConstant('{commonpf}\{#MyAppDirName}')));
  Offer(InstalledData(ExpandConstant('{commonpf32}\{#MyAppDirName}')));
  Home := AddBackslash(GetEnv('USERPROFILE'));
  Offer(Home + '{#MyAppDirName}');
  Offer(Home + 'PersonalizedAgent');
  Offer(AddBackslash(AppDir) + 'data');
  Offer(ExpandConstant('{userpf}\{#MyAppDirName}\data'));
  Offer(ExpandConstant('{commonpf}\{#MyAppDirName}\data'));
  DataFound.Items.Clear;
  for K := 0 to FoundFolders.Count - 1 do begin
    Cut := False;
    Bytes := BytesIn(FoundFolders[K], TicksNow + SIZE_COUNTED_FOR, Cut);
    Shown := LastUsed(FoundFolders[K]);
    if Shown <> '' then Shown := Shown + '    ';
    DataFound.AddRadioButton(FoundFolders[K], Shown + SizeText(Bytes, Cut), 0, False, True, nil);
  end;
  DataFound.Visible := FoundFolders.Count > 0;
  DataFoundTitle.Visible := DataFound.Visible;
end;

// The folder in the box, marked in the list when it is one of them.
procedure MarkChosen;
var
  K: Integer;
begin
  for K := 0 to FoundFolders.Count - 1 do
    DataFound.Checked[K] := SameFolder(FoundFolders[K], DataPage.Values[0]);
end;

// WHETHER WRITING THERE NEEDS AN ADMINISTRATOR. The program works as the user
// who installed it, never as an administrator, so its data folder must be one
// they can write to. The system's own folders by name; and, when this setup is
// not itself running as an administrator, by trying - a folder made and taken
// away again where the data folder would go.
function NeedsAdmin(Dir: String): Boolean;
var
  Folder, Probe, Up, Test: String;
begin
  Result := False;
  if Trim(Dir) = '' then Exit;
  Folder := ExpandFileName(Trim(Dir));
  Result := Under(Folder, ExpandConstant('{commonpf}')) or
            Under(Folder, ExpandConstant('{commonpf32}')) or
            Under(Folder, ExpandConstant('{win}')) or
            Under(Folder, ExpandConstant('{commonappdata}'));
  if Result or IsAdminInstallMode then Exit;
  Probe := RemoveBackslash(Folder);
  while (Probe <> '') and (not DirExists(Probe)) do begin
    Up := ExtractFileDir(Probe);
    if (Up = Probe) or (Up = '') then Exit;
    Probe := Up;
  end;
  Test := AddBackslash(Probe) + '.personalized-agent-write-test';
  if CreateDir(Test) then
    RemoveDir(Test)
  else
    Result := True;
end;

// Next only while the folder is one the user can write to - said why under
// the box, until another is chosen.
procedure FollowDataFolder;
var
  Bad: Boolean;
begin
  Bad := NeedsAdmin(DataPage.Values[0]);
  DataNeedsAdmin.Visible := Bad;
  WizardForm.NextButton.Enabled := not Bad;
  MarkChosen;
end;

procedure DataFolderChanged(Sender: TObject);
begin
  FollowDataFolder;
end;

// One chosen from the list: put in the box.
procedure DataFoundClicked(Sender: TObject);
begin
  if (DataFound.ItemIndex >= 0) and (DataFound.ItemIndex < FoundFolders.Count) then begin
    DataPage.Values[0] := FoundFolders[DataFound.ItemIndex];
    FollowDataFolder;
  end;
end;

// WHAT IS WRITTEN BESIDE THE PROGRAM. As the page was left - but an update,
// or any install without the page, keeps the folder this copy used: also one
// put in data_root.txt by hand, which the record of the last install does
// not know. Never a folder another copy uses, unless the user chose it.
function DataToWrite: String;
begin
  Result := Trim(DataPage.Values[0]);
  if WizardSilent then begin
    Result := CopysData(ExpandConstant('{app}'));
    if Result = '' then Result := Trim(DataPage.Values[0]);
  end;
  if Result = '' then Result := ExpandConstant('{app}\data');
end;

// AN UPDATE: a silent install over a copy already installed, as the
// program's own updater runs it (platform_layer/updater.py).
function AnUpdate: Boolean;
begin
  Result := WizardSilent and (WizardForm.PrevAppDir <> '');
end;

procedure InitializeWizard;
begin
  DataPage := CreateInputDirPage(wpSelectDir, CustomMessage('DataTitle'),
    FmtMessage(CustomMessage('DataDescription'), ['{#MyAppName}']),
    CustomMessage('DataPrompt'), False, '');
  DataPage.Add('');
  PreviousData := GetPreviousData('DataFolder', '');
  DataPage.Values[0] := PreviousData;
  DataDefault := PreviousData;
  DataPage.Edits[0].OnChange := @DataFolderChanged;
  FoundFolders := TStringList.Create;
  FoundFor := #0;
  DataFoundTitle := TNewStaticText.Create(DataPage);
  DataFoundTitle.Parent := DataPage.Surface;
  DataFoundTitle.Left := DataPage.Edits[0].Left;
  DataFoundTitle.Top := DataPage.Edits[0].Top + DataPage.Edits[0].Height + ScaleY(10);
  DataFoundTitle.Width := DataPage.SurfaceWidth - DataPage.Edits[0].Left;
  DataFoundTitle.AutoSize := False;
  DataFoundTitle.WordWrap := True;
  DataFoundTitle.Height := ScaleY(28);
  DataFoundTitle.Caption := FmtMessage(CustomMessage('DataFound'), ['{#MyAppName}']);
  DataFoundTitle.Visible := False;
  DataFound := TNewCheckListBox.Create(DataPage);
  DataFound.Parent := DataPage.Surface;
  DataFound.Left := DataPage.Edits[0].Left;
  DataFound.Top := DataFoundTitle.Top + DataFoundTitle.Height + ScaleY(2);
  DataFound.Width := DataPage.SurfaceWidth - DataPage.Edits[0].Left;
  DataFound.Height := DataPage.SurfaceHeight - DataFound.Top - ScaleY(40);
  if DataFound.Height < ScaleY(48) then DataFound.Height := ScaleY(48);
  DataFound.OnClickCheck := @DataFoundClicked;
  DataFound.Visible := False;
  DataNeedsAdmin := TNewStaticText.Create(DataPage);
  DataNeedsAdmin.Parent := DataPage.Surface;
  DataNeedsAdmin.Left := DataPage.Edits[0].Left;
  DataNeedsAdmin.Top := DataFound.Top + DataFound.Height + ScaleY(4);
  DataNeedsAdmin.Width := DataPage.SurfaceWidth - DataPage.Edits[0].Left;
  DataNeedsAdmin.AutoSize := False;
  DataNeedsAdmin.WordWrap := True;
  DataNeedsAdmin.Height := ScaleY(36);
  DataNeedsAdmin.Font.Color := clRed;
  DataNeedsAdmin.Caption := FmtMessage(CustomMessage('DataProgramFiles'), ['{#MyAppName}']);
  DataNeedsAdmin.Visible := False;
end;

// THE BOX STARTS AT THE FOLDER THIS COPY USED (CopysData) - for a copy
// installed anew, <program folder>\data - following the program folder while
// the user has not typed one of their own. Under it, every folder found
// holding an agent's data, to choose from.
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = DataPage.ID then begin
    if (Trim(DataPage.Values[0]) = '') or SameFolder(DataPage.Values[0], DataDefault) then begin
      DataDefault := CopysData(WizardDirValue);
      if DataDefault = '' then DataDefault := AddBackslash(WizardDirValue) + 'data';
      DataPage.Values[0] := DataDefault;
    end;
    if CompareText(FoundFor, WizardDirValue) <> 0 then begin
      FindFolders(WizardDirValue);
      FoundFor := WizardDirValue;
    end;
    FollowDataFolder;
  end;
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
  if WizardSilent then DataPage.Values[0] := DataToWrite;
  Dir := Trim(DataPage.Values[0]);
  if Dir = '' then begin
    MsgBox(CustomMessage('DataEmpty'), mbError, MB_OK);
    Result := False;
  end else if Under(FolderOf(WizardDirValue), FolderOf(Dir)) then begin
    // NOT THE PROGRAM'S OWN FOLDER, NOR ONE IT IS INSTALLED IN (the user,
    // 2026-10-29): the program's files would sit among the data - and an
    // existing data folder chosen as both was refused by the program at its
    // start, and emptied by uninstalling it.
    MsgBox(FmtMessage(CustomMessage('DataHoldsProgram'), [Dir]), mbError, MB_OK);
    Result := False;
  end else if NeedsAdmin(Dir) then begin
    MsgBox(FmtMessage(CustomMessage('DataProgramFiles'), ['{#MyAppName}']), mbError, MB_OK);
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
                     ExpandFileName(DataToWrite), False);
end;

// The next word of a command line, quoted or not, taken off its front.
function NextToken(var Line: String): String;
var
  P: Integer;
begin
  Line := Trim(Line);
  if Copy(Line, 1, 1) = '"' then begin
    Delete(Line, 1, 1);
    P := Pos('"', Line);
    if P = 0 then P := Length(Line) + 1;
  end else begin
    P := Pos(' ', Line);
    if P = 0 then P := Length(Line) + 1;
  end;
  Result := Copy(Line, 1, P - 1);
  Line := Copy(Line, P + 1, MaxInt);
end;

// The program another copy's sign-in entry starts - '' when there is no
// entry, it is this program's, or what it starts is no longer there (then it
// is nobody's). Run from the source, the program is the run.py Python is given.
function AutostartHolder: String;
var
  Line, Prog, Script: String;
begin
  Result := '';
  if not RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run',
                             'PersonalizedAgent', Line) then Exit;
  Prog := NextToken(Line);
  if Prog = '' then Exit;
  if Pos('python', Lowercase(ExtractFileName(Prog))) = 1 then begin
    Script := NextToken(Line);
    if Lowercase(ExtractFileExt(Script)) = '.py' then Prog := Script;
  end;
  if CompareText(ExpandFileName(Prog), ExpandConstant('{app}\{#MyAppExeName}')) = 0 then Exit;
  if CompareText(ExpandFileName(Prog), ExpandConstant('{app}\{#MyFormerExeName}')) = 0 then Exit;
  if FileExists(Prog) then Result := Prog;
end;

// Left unticked on the last page: said so, so the program's own default
// (on) does not turn it back on at its first start. Ticked while another copy
// already starts at sign-in: refused, until it is unticked.
function NextButtonClick(CurPageID: Integer): Boolean;
var
  ResultCode: Integer;
  Holder: String;
begin
  Result := True;
  if CurPageID = DataPage.ID then
    Result := CheckDataFolder;
  if (CurPageID = wpFinished) and (not WizardSilent) and
     (WizardForm.RunList.Items.Count > 0) and WizardForm.RunList.Checked[0] then begin
    Holder := AutostartHolder;
    if Holder <> '' then begin
      MsgBox(FmtMessage(CustomMessage('StartAtLoginTaken'), ['{#MyAppName}', Holder]),
             mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
  if (CurPageID = wpFinished) and (not WizardSilent) and
     (WizardForm.RunList.Items.Count > 0) and (not WizardForm.RunList.Checked[0]) then
    ExecAsOriginalUser(ExpandConstant('{app}\{#MyAppExeName}'), '--autostart off', '',
                       SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// Whether `Dir` holds an agent's data - in it, or up to `Depth` folders down.
function DataBelow(Dir: String; Depth: Integer): Boolean;
var
  FindRec: TFindRec;
begin
  Result := HasAgentData(Dir);
  if Result or (Depth = 0) then Exit;
  if FindFirst(AddBackslash(Dir) + '*', FindRec) then
  try
    repeat
      if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0) and
         (FindRec.Attributes and REPARSE_POINT = 0) and
         (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        Result := DataBelow(AddBackslash(Dir) + FindRec.Name, Depth - 1);
    until Result or (not FindNext(FindRec));
  finally
    FindClose(FindRec);
  end;
end;

// UNINSTALLING TAKES THE PROGRAM AND LEAVES THE DATA. The data folder is
// under the program's by default, so the program's folder is emptied here
// rather than deleted whole: everything in it goes except the folder the data
// is in. Removing the data is the user's own act.
// AND NEVER AN AGENT'S DATA, WHATEVER data_root.txt SAYS (the user,
// 2026-10-29): with the file gone or unreadable, nothing was known to keep
// and the default data folder went with the program; with the program's own
// folder as the data folder, the folder to keep was the one being emptied.
// So a workspace or internal folder is never deleted, nor any folder holding
// an agent's database or settings two folders down.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Raw: AnsiString;
  App, Keep, Name: String;
  FindRec: TFindRec;
begin
  if CurUninstallStep = usUninstall then begin
    if LoadStringFromFile(ExpandConstant('{app}\data_root.txt'), Raw) then
      UninstallDataRoot := Trim(String(Raw));
  end;
  if CurUninstallStep = usPostUninstall then begin
    App := AddBackslash(ExpandConstant('{app}'));
    Keep := '';
    if (UninstallDataRoot <> '') and Under(UninstallDataRoot, App) then begin
      Keep := Copy(AddBackslash(ExpandFileName(UninstallDataRoot)), Length(App) + 1, MaxInt);
      if Pos('\', Keep) > 0 then Keep := Copy(Keep, 1, Pos('\', Keep) - 1);
    end;
    if FindFirst(App + '*', FindRec) then
    try
      repeat
        Name := FindRec.Name;
        if (Name <> '.') and (Name <> '..') and ((Keep = '') or (CompareText(Name, Keep) <> 0)) then begin
          if FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then begin
            if (CompareText(Name, 'workspace') <> 0) and (CompareText(Name, 'internal') <> 0) and
               (not DataBelow(App + Name, 2)) then
              DelTree(App + Name, True, True, True);
          end else
            DeleteFile(App + Name);
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
    RemoveDir(App);
  end;
end;

{
  TES5_Import_Records.pas
  =======================
  xEdit Pascal Script for TES V (Skyrim) - Import Phase

  Reads the exported TES4 data from TES4_Records.txt and creates
  corresponding records in a Skyrim plugin.

  Usage:
    1. Create a new .esp in TES5Edit (SSEEdit for Skyrim SE)
    2. Select the new plugin
    3. Apply this script
    4. It reads from <DataPath>\TES4Export\TES4_Records.txt
    5. Records are created in the selected plugin

  IMPORTANT NOTES:
  - FormIDs from TES4 cannot be directly reused. This script creates NEW
    records. Cross-references (e.g., a weapon enchantment pointing at an ENCH)
    will need manual relinking after import.
  - Models/meshes/textures need to be manually converted from Oblivion format
    to Skyrim format (NIF version, DDS compression, etc.)
  - TES4 scripts (SCPT) must be manually rewritten in Papyrus.
  - VMAD (Papyrus script attachments) cannot be auto-generated.
  - Some record types have no direct equivalent and are approximated.

  Record Conversion Notes:
  - APPA (Apparatus) -> MISC (no alchemy apparatus in TES5)
  - BSGN (Birthsign) -> spells added to a race or Standing Stone
  - CLOT (Clothing)  -> ARMO with ArmorType = Clothing
  - CREA (Creature)  -> NPC_ (no CREA record in TES5)
  - HAIR (Hair)      -> HDPT (Head Part)  
  - LVLC (Lev.Crea.) -> LVLN (Leveled NPC)
  - SBSP (Subspace)  -> STAT (Static)
  - SGST (Sigil Stone)-> SCRL (Scroll)
  - ACRE (Placed Creature) -> ACHR (Placed NPC)
}
unit TES5_Import_Records;

var
  slImport: TStringList;
  TargetPlugin: IInterface;
  ImportPath: string;
  CurrentLine: Integer;
  RecordCount: Integer;
  SkippedCount: Integer;
  // FormID map: old TES4 FormID (string) -> new TES5 record
  slFormIDMap: TStringList;
  recData: TStringList;


//============================================================================
// Utility: Parsing Functions
//============================================================================

function UnescapeStr(s: string): string;
begin
  Result := s;
  Result := StringReplace(Result, '\\', Chr(1), [rfReplaceAll]);
  Result := StringReplace(Result, '\"', '"', [rfReplaceAll]);
  Result := StringReplace(Result, '\r', Chr(13), [rfReplaceAll]);
  Result := StringReplace(Result, '\n', Chr(10), [rfReplaceAll]);
  Result := StringReplace(Result, '\t', Chr(9), [rfReplaceAll]);
  Result := StringReplace(Result, Chr(1), '\', [rfReplaceAll]);
end;

// Read all lines between ---RECORD_BEGIN--- and ---RECORD_END--- into recData
function ReadNextRecord(): Boolean;
var
  line: string;
begin
  Result := False;
  recData.Clear;
  
  // Find next RECORD_BEGIN
  while CurrentLine < slImport.Count do begin
    line := slImport.Strings[CurrentLine];
    Inc(CurrentLine);
    if line = '---RECORD_BEGIN---' then begin
      Result := True;
      Break;
    end;
  end;
  
  if not Result then Exit;
  
  // Read until RECORD_END
  while CurrentLine < slImport.Count do begin
    line := slImport.Strings[CurrentLine];
    Inc(CurrentLine);
    if line = '---RECORD_END---' then
      Break;
    if (line <> '') and (Copy(line, 1, 1) <> '#') then
      recData.Add(line);
  end;
end;

// recData uses KEY=VALUE format; TStringList.Values[] looks up by key natively
function RecordValue(key: string): string;
begin
  Result := recData.Values[key];
end;

function RecordValueInt(key: string): Integer;
begin
  Result := StrToIntDef(recData.Values[key], 0);
end;

function RecordValueFloat(key: string): Double;
begin
  Result := StrToFloatDef(recData.Values[key], 0.0);
end;

function RecordHasKey(key: string): Boolean;
begin
  Result := recData.IndexOfName(key) >= 0;
end;

//============================================================================
// Record Creation Helpers
//============================================================================

function GetGroupBySignature(plugin: IInterface; const sig: string): IInterface;
var
  i: Integer;
  grp: IInterface;
begin
  Result := nil;
  for i := 0 to ElementCount(plugin) - 1 do begin
    grp := ElementByIndex(plugin, i);
    if Signature(grp) = 'GRUP' then begin
      if GetElementEditValues(grp, 'Group Type') = '0' then begin
        // Top-level group - check label
        if Pos(sig, Name(grp)) > 0 then begin
          Result := grp;
          Exit;
        end;
      end;
    end;
  end;
end;

function CreateNewRecord(plugin: IInterface; const sig: string): IInterface;
var
  grp: IInterface;
begin
  // Add(plugin, sig, True) finds-or-creates the top-level group in the plugin
  grp := Add(plugin, sig, True);
  if not Assigned(grp) then begin
    Result := nil;
    Exit;
  end;
  // Add(group, sig, True) creates a new record inside that group
  Result := Add(grp, sig, True);
end;

procedure SetEditorID(rec: IInterface; const edid: string);
begin
  if edid <> '' then begin
    Add(rec, 'EDID', True);
    SetElementEditValues(rec, 'EDID', edid);
  end;
end;

procedure SetFull(rec: IInterface; const name: string);
begin
  if name <> '' then begin
    Add(rec, 'FULL', True);
    SetElementEditValues(rec, 'FULL', UnescapeStr(name));
  end;
end;

procedure SetDescription(rec: IInterface; const desc: string);
begin
  if desc <> '' then begin
    Add(rec, 'DESC', True);
    SetElementEditValues(rec, 'DESC', UnescapeStr(desc));
  end;
end;

procedure SetModel(rec: IInterface; const modelPath: string);
begin
  if modelPath <> '' then begin
    Add(rec, 'Model', True);
    SetElementEditValues(rec, 'Model\MODL', modelPath);
  end;
end;

procedure SetIcon(rec: IInterface);
var
  icon: string;
begin
  icon := RecordValue('ICON');
  if icon <> '' then begin
    // TES5 uses ICON inside an unnamed struct for most records
    Add(rec, 'ICON', True);
    SetElementEditValues(rec, 'ICON', icon);
  end;
end;

procedure StoreFormIDMapping(const oldFormID: string; newRec: IInterface);
begin
  if (oldFormID <> '') and Assigned(newRec) then
    slFormIDMap.Values[oldFormID] := IntToHex(GetLoadOrderFormID(newRec), 8);
end;

//============================================================================
// Actor Value Mapping (TES4 index -> TES5 index)
//============================================================================

function MapTES4ActorValueToTES5(tes4AV: Integer): Integer;
begin
  case tes4AV of
    8:  Result := 24;  // Health
    9:  Result := 25;  // Magicka
    10: Result := 26;  // Stamina (Fatigue)
    15: Result := 9;   // Block
    18: Result := 10;  // Heavy Armor
    27: Result := 12;  // Light Armor
    31: Result := 15;  // Sneak
    19: Result := 16;  // Alchemy
    32: Result := 17;  // Speech (Speechcraft)
    20: Result := 18;  // Alteration
    21: Result := 19;  // Conjuration
    22: Result := 20;  // Destruction
    23: Result := 21;  // Illusion
    25: Result := 22;  // Restoration
    28: Result := 8;   // Archery (Marksman)
    14: Result := 7;   // One-Handed (Blade)
    16: Result := 7;   // One-Handed (Blunt)
    17: Result := 7;   // One-Handed (Hand to Hand)
    12: Result := 11;  // Smithing (Armorer)
    30: Result := 13;  // Lockpicking (Security)
    29: Result := 14;  // Pickpocket (Mercantile)
    24: Result := 21;  // Illusion (Mysticism)
  else
    Result := -1;
  end;
end;

//============================================================================
// Skill Mapping for RACE skill boosts
//============================================================================

function MapTES4SkillToTES5(tes4Skill: Integer): Integer;
begin
  // TES4 skill indices (from wbMajorSkillEnum):
  // 12=Armorer,13=Athletics,14=Blade,15=Block,16=Blunt,17=HandToHand,
  // 18=HeavyArmor,19=Alchemy,20=Alteration,21=Conjuration,22=Destruction,
  // 23=Illusion,24=Mysticism,25=Restoration,26=Acrobatics,27=LightArmor,
  // 28=Marksman,29=Mercantile,30=Security,31=Sneak,32=Speechcraft
  // TES5 skills (from wbSkillEnum):
  // 6=OneHanded,7=TwoHanded,8=Archery,9=Block,10=Smithing,11=HeavyArmor,
  // 12=LightArmor,13=Pickpocket,14=Lockpicking,15=Sneak,16=Alchemy,
  // 17=Speech,18=Alteration,19=Conjuration,20=Destruction,21=Illusion,
  // 22=Restoration,23=Enchanting
  case tes4Skill of
    12: Result := 10;  // Armorer -> Smithing
    13: Result := -1;  // Athletics -> None
    14: Result := 6;   // Blade -> One Handed
    15: Result := 9;   // Block -> Block
    16: Result := 6;   // Blunt -> One Handed
    17: Result := 6;   // Hand to Hand -> One Handed
    18: Result := 11;  // Heavy Armor -> Heavy Armor
    19: Result := 16;  // Alchemy -> Alchemy
    20: Result := 18;  // Alteration -> Alteration
    21: Result := 19;  // Conjuration -> Conjuration
    22: Result := 20;  // Destruction -> Destruction
    23: Result := 21;  // Illusion -> Illusion
    24: Result := 21;  // Mysticism -> Illusion
    25: Result := 22;  // Restoration -> Restoration
    26: Result := -1;  // Acrobatics -> None
    27: Result := 12;  // Light Armor -> Light Armor
    28: Result := 8;   // Marksman -> Archery
    29: Result := 13;  // Mercantile -> Pickpocket
    30: Result := 14;  // Security -> Lockpicking
    31: Result := 15;  // Sneak -> Sneak
    32: Result := 17;  // Speechcraft -> Speech
  else
    Result := -1;
  end;
end;

//============================================================================
// Magic Effect Conversion
//============================================================================
// TES4 uses 4-char MGEF codes; TES5 uses FormID-based MGEF references.
// This is a best-effort mapping for common effects.

function MapMGEFCode(const code: string): string;
begin
  // Returns the TES5 EditorID equivalent (user must manually resolve FormIDs)
  // Common Oblivion effects -> Skyrim equivalents
  if code = 'REFA' then Result := 'AbRestoreHealth'      // Restore Health
  else if code = 'REHE' then Result := 'AbRestoreHealth'
  else if code = 'REMA' then Result := 'AbRestoreMagicka'  // Restore Magicka
  else if code = 'REFA' then Result := 'AbRestoreStamina'  // Restore Fatigue -> Stamina
  else if code = 'DRHE' then Result := 'AbDamageHealth'    // Drain Health
  else if code = 'DRMA' then Result := 'AbDamageMagicka'
  else if code = 'DRFA' then Result := 'AbDamageStamina'
  else if code = 'FOHE' then Result := 'AbFortifyHealth'   // Fortify Health
  else if code = 'FOMA' then Result := 'AbFortifyMagicka'
  else if code = 'FOFA' then Result := 'AbFortifyStamina'
  else if code = 'RSFI' then Result := 'AbResistFire'
  else if code = 'RSFR' then Result := 'AbResistFrost'
  else if code = 'RSSH' then Result := 'AbResistShock'
  else if code = 'RSPO' then Result := 'AbResistPoison'
  else if code = 'RSMA' then Result := 'AbResistMagic'
  else if code = 'RSDI' then Result := 'AbResistDisease'
  else if code = 'INVI' then Result := 'InvisibilityFFSelf'
  else if code = 'CHML' then Result := 'InvisibilityFFSelf' // Chameleon -> Invis
  else if code = 'PARA' then Result := 'ParalysisFFContact'
  else if code = 'SLNC' then Result := 'AbSilence'
  else if code = 'WABR' then Result := 'AbWaterbreathing'
  else if code = 'WAWA' then Result := 'AbWaterwalking'
  else Result := ''; // Unknown - needs manual mapping
end;

//============================================================================
// Import Functions for Each Record Type
//============================================================================

procedure ImportACTI();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'ACTI');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportALCH();
var
  rec: IInterface;
  value, flags: Integer;
  weight: Double;
begin
  rec := CreateNewRecord(TargetPlugin, 'ALCH');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  // ENIT data
  Add(rec, 'ENIT', True);
  value := RecordValueInt('ENIT.Value');
  SetElementNativeValues(rec, 'ENIT\Value', value);
  
  weight := RecordValueFloat('Weight');
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Weight', weight);
  
  // Note: Effects need MGEF FormID resolution
  // TES4 effects use 4-char codes; TES5 requires FormID references
  // Exported effect data stored as comments for manual fixup
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportAMMO();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'AMMO');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  // DATA - TES5 AMMO DATA structure
  Add(rec, 'DATA', True);
  // TES5 AMMO: DNAM has Projectile, rest in DATA
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  SetElementNativeValues(rec, 'DATA\Damage', RecordValueInt('DATA.Damage'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportARMO();
var
  rec: IInterface;
  bipedSlots, armorType: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'ARMO');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  // BOD2 - Body Template (TES5 uses BOD2 instead of BMDT)
  bipedSlots := RecordValueInt('TES5.BipedSlots');
  armorType := RecordValueInt('ArmorType');
  
  Add(rec, 'BOD2', True);
  SetElementNativeValues(rec, 'BOD2\First Person Flags', bipedSlots);
  SetElementNativeValues(rec, 'BOD2\Armor Type', armorType);
  
  // Male world model
  if RecordValue('Male.WorldModel') <> '' then begin
    Add(rec, 'Male world model', True);
    SetElementEditValues(rec, 'Male world model\MOD2', RecordValue('Male.WorldModel'));
  end;
  
  // Female world model
  if RecordValue('Female.WorldModel') <> '' then begin
    Add(rec, 'Female world model', True);
    SetElementEditValues(rec, 'Female world model\MOD4', RecordValue('Female.WorldModel'));
  end;
  
  // DATA
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  // DNAM (Armor Rating in TES5)
  Add(rec, 'DNAM', True);
  SetElementNativeValues(rec, 'DNAM', RecordValueInt('DATA.Armor'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportBOOK();
var
  rec: IInterface;
  flags, teaches: Integer;
  tes5Skill: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'BOOK');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  SetDescription(rec, RecordValue('DESC'));
  
  // DATA
  flags := RecordValueInt('DATA.Flags');
  teaches := RecordValueInt('DATA.Teaches');
  
  Add(rec, 'DATA', True);
  // TES5 BOOK flags: bit 0 = teaches skill, bit 2 = can't be taken (note flag)
  if teaches >= 0 then begin
    tes5Skill := MapTES4SkillToTES5(teaches + 12); // TES4 skill enum offset
    if tes5Skill >= 0 then begin
      SetElementNativeValues(rec, 'DATA\Flags', 1);  // Teaches skill
      SetElementNativeValues(rec, 'DATA\Teaches', tes5Skill);
    end;
  end;
  
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportCELL();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'CELL');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA', RecordValueInt('DATA.Flags'));
  
  // Lighting (interior cells)
  if RecordHasKey('XCLL.Ambient') then begin
    Add(rec, 'XCLL', True);
    SetElementEditValues(rec, 'XCLL\Ambient Color', RecordValue('XCLL.Ambient'));
    SetElementEditValues(rec, 'XCLL\Directional Color', RecordValue('XCLL.Directional'));
    SetElementEditValues(rec, 'XCLL\Fog Color Near', RecordValue('XCLL.Fog'));
    SetElementNativeValues(rec, 'XCLL\Fog Near', RecordValueFloat('XCLL.FogNear'));
    SetElementNativeValues(rec, 'XCLL\Fog Far', RecordValueFloat('XCLL.FogFar'));
    SetElementNativeValues(rec, 'XCLL\Directional Rotation XY', RecordValueInt('XCLL.DirRotXY'));
    SetElementNativeValues(rec, 'XCLL\Directional Rotation Z', RecordValueInt('XCLL.DirRotZ'));
    SetElementNativeValues(rec, 'XCLL\Directional Fade', RecordValueFloat('XCLL.DirFade'));
    SetElementNativeValues(rec, 'XCLL\Fog Clip Dist', RecordValueFloat('XCLL.FogClip'));
  end;
  
  // Grid (exterior cells)
  if RecordHasKey('XCLC.X') then begin
    Add(rec, 'XCLC', True);
    SetElementNativeValues(rec, 'XCLC\X', RecordValueInt('XCLC.X'));
    SetElementNativeValues(rec, 'XCLC\Y', RecordValueInt('XCLC.Y'));
  end;
  
  // Water height
  if RecordHasKey('XCLW') then begin
    Add(rec, 'XCLW', True);
    SetElementNativeValues(rec, 'XCLW', RecordValueFloat('XCLW'));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportCLAS();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'CLAS');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetDescription(rec, RecordValue('DESC'));
  
  // TES5 CLAS DATA is simpler - no attributes/skills
  Add(rec, 'DATA', True);
  // TES5 class DATA has: Flags, Teaches, MaxTrainingLevel, SkillWeights
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Flags'));
  SetElementNativeValues(rec, 'DATA\Teaches',
    MapTES4ActorValueToTES5(RecordValueInt('DATA.Teaches')));
  SetElementNativeValues(rec, 'DATA\Maximum Training Level',
    RecordValueInt('DATA.MaxTraining'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportCLMT();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'CLMT');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // Weather types need FormID resolution
  // Sun texture
  if RecordValue('SunTexture') <> '' then begin
    Add(rec, 'FNAM', True);
    SetElementEditValues(rec, 'FNAM', RecordValue('SunTexture'));
  end;
  if RecordValue('SunGlare') <> '' then begin
    Add(rec, 'GNAM', True);
    SetElementEditValues(rec, 'GNAM', RecordValue('SunGlare'));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportCONT();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'CONT');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  // DATA
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Flags'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  // Items need FormID resolution - export count for reference
  // AddMessage('  CONT items: ' + RecordValue('ItemCount') + ' - need manual relinking');
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportNPC();
var
  rec: IInterface;
  flags: Integer;
  origType: string;
  health, magicka, stamina: Integer;
  aggression, confidence, responsibility: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'NPC_');
  if not Assigned(rec) then Exit;
  
  origType := RecordValue('OriginalType');
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  // ACBS Configuration
  Add(rec, 'ACBS', True);
  flags := RecordValueInt('ACBS.Flags');
  
  // Remap TES4 NPC flags to TES5
  // TES4: 0=Female,1=Essential,3=Respawn,4=AutoCalc,7=PCLevelOffset,9=NoLowLevel,13=NoRumors,14=Summonable,15=NoPersuasion,20=CorpseCheck
  // TES5: 0=Female,1=Essential,3=Respawn,4=AutoCalcStats,7=PCLevelMult,8=UsesTemplate,9=Protected,...
  // Most flags map directly for bits 0-7
  SetElementNativeValues(rec, 'ACBS\Flags', flags);
  
  // Calculate TES5 health/magicka/stamina from TES4 attributes
  if origType = 'CREA' then begin
    health := RecordValueInt('DATA.Health');
    magicka := RecordValueInt('ACBS.SpellPoints');
    stamina := RecordValueInt('ACBS.Fatigue');
  end else begin
    // NPC: derive from TES4 attributes
    health := RecordValueInt('DATA.Health');
    if health = 0 then
      health := RecordValueInt('DATA.Endurance') * 2;
    magicka := RecordValueInt('DATA.Intelligence') * 2;
    stamina := RecordValueInt('DATA.Endurance') + RecordValueInt('DATA.Agility');
  end;
  
  SetElementNativeValues(rec, 'ACBS\Health Offset', health);
  SetElementNativeValues(rec, 'ACBS\Magicka Offset', magicka);
  SetElementNativeValues(rec, 'ACBS\Stamina Offset', stamina);
  SetElementNativeValues(rec, 'ACBS\Level', RecordValueInt('ACBS.Level'));
  SetElementNativeValues(rec, 'ACBS\Calc min level', RecordValueInt('ACBS.CalcMin'));
  SetElementNativeValues(rec, 'ACBS\Calc max level', RecordValueInt('ACBS.CalcMax'));
  SetElementNativeValues(rec, 'ACBS\Speed Multiplier', 100);
  SetElementNativeValues(rec, 'ACBS\Disposition Base', 35);
  SetElementNativeValues(rec, 'ACBS\Barter gold', RecordValueInt('ACBS.BarterGold'));
  
  // AI Data
  Add(rec, 'AIDT', True);
  // TES4 aggression: 0-100 scale. TES5: 0=Unaggressive, 1=Aggressive, 2=VeryAggressive, 3=Frenzied
  aggression := RecordValueInt('AIDT.Aggression');
  if aggression <= 5 then
    SetElementNativeValues(rec, 'AIDT\Aggression', 0)
  else if aggression <= 50 then
    SetElementNativeValues(rec, 'AIDT\Aggression', 1)
  else if aggression <= 80 then
    SetElementNativeValues(rec, 'AIDT\Aggression', 2)
  else
    SetElementNativeValues(rec, 'AIDT\Aggression', 3);
  
  // TES4 confidence: 0-100. TES5: 0=Cowardly,1=Cautious,2=Average,3=Brave,4=Foolhardy
  confidence := RecordValueInt('AIDT.Confidence');
  if confidence < 20 then
    SetElementNativeValues(rec, 'AIDT\Confidence', 0)
  else if confidence < 40 then
    SetElementNativeValues(rec, 'AIDT\Confidence', 1)
  else if confidence < 60 then
    SetElementNativeValues(rec, 'AIDT\Confidence', 2)
  else if confidence < 80 then
    SetElementNativeValues(rec, 'AIDT\Confidence', 3)
  else
    SetElementNativeValues(rec, 'AIDT\Confidence', 4);
  
  SetElementNativeValues(rec, 'AIDT\Energy Level', RecordValueInt('AIDT.EnergyLevel'));
  
  // TES4 responsibility: 0-100. TES5: 0=Any Crime,1=Violence Against Enemies,2=Property Crime Only,3=No Crime
  responsibility := RecordValueInt('AIDT.Responsibility');
  if responsibility < 20 then
    SetElementNativeValues(rec, 'AIDT\Responsibility', 0)
  else if responsibility < 50 then
    SetElementNativeValues(rec, 'AIDT\Responsibility', 1)
  else if responsibility < 80 then
    SetElementNativeValues(rec, 'AIDT\Responsibility', 2)
  else
    SetElementNativeValues(rec, 'AIDT\Responsibility', 3);
  
  // Note: Race, Class, Hair, Eyes, Factions, Spells, Items, Packages
  // all need FormID resolution after all records are created.
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportDIAL();
var
  rec: IInterface;
  tes4Type, tes5Type: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'DIAL');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  // TES4 dialog types: 0=Topic,1=Conversation,2=Combat,3=Persuasion,4=Detection,5=Service,6=Misc
  // TES5 dialog types: 0=Topic,1=Conversation,2=Combat,3=Persuasion,4=Detection,5=Service,6=Misc,7=CustomTopic,...
  tes4Type := RecordValueInt('DATA.Type');
  tes5Type := tes4Type; // Direct mapping for 0-6
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Category', tes5Type);
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportDOOR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'DOOR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'FNAM', True);
  SetElementNativeValues(rec, 'FNAM', RecordValueInt('FNAM.Flags'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportEFSH();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'EFSH');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // Fill texture
  if RecordValue('FillTexture') <> '' then begin
    Add(rec, 'ICON', True);
    SetElementEditValues(rec, 'ICON', RecordValue('FillTexture'));
  end;
  
  // TES5 EFSH DATA structure differs significantly from TES4
  // Basic flag mapping
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Flags'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportENCH();
var
  rec: IInterface;
  tes4Type, tes5Type: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'ENCH');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  // ENIT
  // TES4 types: 0=Scroll,1=Staff,2=Weapon,3=Apparel
  // TES5 types: 6=Enchantment, 12=Staff Enchantment
  tes4Type := RecordValueInt('ENIT.Type');
  case tes4Type of
    0: tes5Type := 6;   // Scroll -> Enchantment
    1: tes5Type := 12;  // Staff -> Staff Enchantment
    2: tes5Type := 6;   // Weapon -> Enchantment
    3: tes5Type := 6;   // Apparel -> Enchantment
  else
    tes5Type := 6;
  end;
  
  Add(rec, 'ENIT', True);
  SetElementNativeValues(rec, 'ENIT\Type', tes5Type);
  SetElementNativeValues(rec, 'ENIT\Charge Amount', RecordValueInt('ENIT.Charge'));
  SetElementNativeValues(rec, 'ENIT\Enchantment Cost', RecordValueInt('ENIT.Cost'));
  
  // Effects need MGEF FormID resolution
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportEYES();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'EYES');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  if RecordValue('ICON') <> '' then begin
    Add(rec, 'ICON', True);
    SetElementEditValues(rec, 'ICON', RecordValue('ICON'));
  end;
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Playable'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportFACT();
var
  rec: IInterface;
  tes4Flags, tes5Flags: Integer;
  rankCount, i: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'FACT');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  // DATA flags
  // TES4: 0=HiddenFromPC,1=Evil,2=SpecialCombat
  // TES5: 0=HiddenFromPC,1=SpecialCombat,2=TrackCrime,...
  tes4Flags := RecordValueInt('DATA.Flags');
  tes5Flags := 0;
  if (tes4Flags and 1) <> 0 then tes5Flags := tes5Flags or 1;  // Hidden
  if (tes4Flags and 4) <> 0 then tes5Flags := tes5Flags or 2;  // Special Combat
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', tes5Flags);
  
  // Crime gold multiplier
  Add(rec, 'CRVA', True); // TES5 uses CRVA instead of CNAM for crime data
  SetElementNativeValues(rec, 'CRVA\Murder',
    Round(RecordValueFloat('CNAM.CrimeGoldMult') * 1000));
  
  // Ranks
  rankCount := RecordValueInt('RankCount');
  if rankCount > 0 then begin
    for i := 0 to rankCount - 1 do begin
      Add(rec, 'Ranks', True);
      // TES5 faction ranks: RNAM (number), MNAM (male), FNAM (female)
    end;
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportFLOR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'FLOR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  // Ingredient needs FormID resolution
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportFURN();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'FURN');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportGLOB();
var
  rec: IInterface;
  typeStr: string;
begin
  rec := CreateNewRecord(TargetPlugin, 'GLOB');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  typeStr := RecordValue('FNAM.Type');
  Add(rec, 'FNAM', True);
  SetElementEditValues(rec, 'FNAM', typeStr);
  
  Add(rec, 'FLTV', True);
  SetElementNativeValues(rec, 'FLTV', RecordValueFloat('FLTV.Value'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportGMST();
var
  rec: IInterface;
  edid: string;
begin
  rec := CreateNewRecord(TargetPlugin, 'GMST');
  if not Assigned(rec) then Exit;
  
  edid := RecordValue('EditorID');
  SetEditorID(rec, edid);
  
  // GMST value type determined by first char of EditorID
  Add(rec, 'DATA', True);
  SetElementEditValues(rec, 'DATA', RecordValue('DATA.Value'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportGRAS();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'GRAS');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Density', RecordValueInt('DATA.Density'));
  SetElementNativeValues(rec, 'DATA\Min Slope', RecordValueInt('DATA.MinSlope'));
  SetElementNativeValues(rec, 'DATA\Max Slope', RecordValueInt('DATA.MaxSlope'));
  SetElementNativeValues(rec, 'DATA\Units From Water Amount', RecordValueInt('DATA.UnitFromWater'));
  SetElementNativeValues(rec, 'DATA\Units From Water Type', RecordValueInt('DATA.UnitFromWaterType'));
  SetElementNativeValues(rec, 'DATA\Position Range', RecordValueFloat('DATA.PosRange'));
  SetElementNativeValues(rec, 'DATA\Height Range', RecordValueFloat('DATA.HeightRange'));
  SetElementNativeValues(rec, 'DATA\Color Range', RecordValueFloat('DATA.ColorRange'));
  SetElementNativeValues(rec, 'DATA\Wave Period', RecordValueFloat('DATA.WavePeriod'));
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Flags'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportHDPT();
// HAIR -> HDPT (Head Part)
var
  rec: IInterface;
  tes4Flags: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'HDPT');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  // TES5 HDPT DATA: Type enum (0=Misc,1=Face,2=Eyes,3=Hair,4=FacialHair,5=Scar,6=Eyebrows)
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', 1); // Playable
  
  // PNAM (Type) = 3 (Hair)
  Add(rec, 'PNAM', True);
  SetElementNativeValues(rec, 'PNAM', 3);
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportIDLE();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'IDLE');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportINGR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'INGR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  // ENIT
  Add(rec, 'ENIT', True);
  SetElementNativeValues(rec, 'ENIT\Value', RecordValueInt('ENIT.Value'));
  
  // DATA Weight
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  // Effects need MGEF resolution
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportKEYM();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'KEYM');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportLIGH();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'LIGH');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Time', RecordValueInt('DATA.Time'));
  SetElementNativeValues(rec, 'DATA\Radius', RecordValueInt('DATA.Radius'));
  SetElementEditValues(rec, 'DATA\Color', RecordValue('DATA.Color'));
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Flags'));
  SetElementNativeValues(rec, 'DATA\Falloff Exponent', RecordValueFloat('DATA.FalloffExp'));
  SetElementNativeValues(rec, 'DATA\FOV', RecordValueFloat('DATA.FOV'));
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  Add(rec, 'FNAM', True);
  SetElementNativeValues(rec, 'FNAM', RecordValueFloat('FNAM.Fade'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportLSCR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'LSCR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetDescription(rec, RecordValue('DESC'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportLTEX();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'LTEX');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // TES5 LTEX uses TNAM (texture set ref) instead of ICON
  // HNAM material data
  Add(rec, 'HNAM', True);
  SetElementNativeValues(rec, 'HNAM\Friction', RecordValueInt('HNAM.Friction'));
  SetElementNativeValues(rec, 'HNAM\Restitution', RecordValueInt('HNAM.Restitution'));
  
  Add(rec, 'SNAM', True);
  SetElementNativeValues(rec, 'SNAM', RecordValueInt('SNAM.Specular'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportLVLN();
// LVLC -> LVLN (Leveled NPC)
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'LVLN');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  Add(rec, 'LVLD', True);
  SetElementNativeValues(rec, 'LVLD', RecordValueInt('LVLD.ChanceNone'));
  
  Add(rec, 'LVLF', True);
  SetElementNativeValues(rec, 'LVLF', RecordValueInt('LVLF.Flags'));
  
  // Entries need FormID resolution
  // TES5 uses LLCT for count
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportLVLI();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'LVLI');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  Add(rec, 'LVLD', True);
  SetElementNativeValues(rec, 'LVLD', RecordValueInt('LVLD.ChanceNone'));
  
  Add(rec, 'LVLF', True);
  SetElementNativeValues(rec, 'LVLF', RecordValueInt('LVLF.Flags'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportLVSP();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'LVSP');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  Add(rec, 'LVLD', True);
  SetElementNativeValues(rec, 'LVLD', RecordValueInt('LVLD.ChanceNone'));
  
  Add(rec, 'LVLF', True);
  SetElementNativeValues(rec, 'LVLF', RecordValueInt('LVLF.Flags'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportMGEF();
var
  rec: IInterface;
  tes4Flags, tes5Flags: Integer;
  school, tes5AV: Integer;
  resistVal: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'MGEF');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetDescription(rec, RecordValue('DESC'));
  
  // TES5 MGEF uses a completely different data structure
  // DATA subrecord in TES5 has: Flags, Base Cost, Assoc Item, Magic Skill,
  //   Resist Value, Counter Effect Count, etc.
  Add(rec, 'DATA', True);
  
  // Map TES4 flags to TES5
  tes4Flags := RecordValueInt('DATA.Flags');
  tes5Flags := 0;
  if (tes4Flags and 1) <> 0 then tes5Flags := tes5Flags or 1;       // Hostile
  if (tes4Flags and 2) <> 0 then tes5Flags := tes5Flags or 2;       // Recover
  if (tes4Flags and 4) <> 0 then tes5Flags := tes5Flags or 4;       // Detrimental
  if (tes4Flags and $800) <> 0 then tes5Flags := tes5Flags or $200;  // Spellmaking -> No Area
  
  SetElementNativeValues(rec, 'DATA\Flags', tes5Flags);
  SetElementNativeValues(rec, 'DATA\Base Cost', RecordValueFloat('DATA.BaseCost'));
  
  // Magic School mapping: TES4 0-5 -> TES5 Magic Skill ActorValue
  // TES4: 0=Alteration,1=Conjuration,2=Destruction,3=Illusion,4=Mysticism,5=Restoration
  // TES5 skills: 18=Alteration,19=Conjuration,20=Destruction,21=Illusion,22=Restoration
  school := RecordValueInt('DATA.MagicSchool');
  case school of
    0: tes5AV := 18; // Alteration
    1: tes5AV := 19; // Conjuration
    2: tes5AV := 20; // Destruction
    3: tes5AV := 21; // Illusion
    4: tes5AV := 21; // Mysticism -> Illusion
    5: tes5AV := 22; // Restoration
  else
    tes5AV := -1;
  end;
  SetElementNativeValues(rec, 'DATA\Magic Skill', tes5AV);
  
  // Resist value mapping
  resistVal := RecordValueInt('DATA.ResistValue');
  SetElementNativeValues(rec, 'DATA\Resist Value', MapTES4ActorValueToTES5(resistVal));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportMISC();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'MISC');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportPACK();
var
  rec: IInterface;
begin
  // TES5 Package system is completely different (procedural tree-based)
  // We create a basic skeleton
  rec := CreateNewRecord(TargetPlugin, 'PACK');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // TES5 PKDT is very different. Mark for manual review.
  Add(rec, 'PKDT', True);
  // Basic flags only
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportQUST();
var
  rec: IInterface;
  stageCount, logCount, targetCount: Integer;
  i, j: Integer;
  prefix: string;
begin
  rec := CreateNewRecord(TargetPlugin, 'QUST');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  // DNAM (Data) in TES5
  Add(rec, 'DNAM', True);
  // TES5 quest flags: 0=StartGameEnabled,1=Completed,2=AddIdleTopicToHello,...
  SetElementNativeValues(rec, 'DNAM\Flags', RecordValueInt('DATA.Flags'));
  SetElementNativeValues(rec, 'DNAM\Priority', RecordValueInt('DATA.Priority'));
  
  // Stages
  stageCount := RecordValueInt('StageCount');
  if stageCount > 0 then begin
    for i := 0 to stageCount - 1 do begin
      prefix := 'Stage[' + IntToStr(i) + ']';
      // TES5 quest stages are similar but have different sub-structures
      // Would need Add(rec, 'Stages', True) and populate
      // For now, log as needing manual fixup
    end;
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportRACE();
var
  rec: IInterface;
  boostCount, i, tes4Skill, tes5Skill: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'RACE');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetDescription(rec, RecordValue('DESC'));
  
  // DATA - TES5 RACE DATA is completely different structure
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Male Height', RecordValueFloat('DATA.MaleHeight'));
  SetElementNativeValues(rec, 'DATA\Female Height', RecordValueFloat('DATA.FemaleHeight'));
  SetElementNativeValues(rec, 'DATA\Male Weight', RecordValueFloat('DATA.MaleWeight'));
  SetElementNativeValues(rec, 'DATA\Female Weight', RecordValueFloat('DATA.FemaleWeight'));
  
  // Skill boosts mapping
  boostCount := RecordValueInt('SkillBoostCount');
  // TES5 RACE has fixed 7 skill boost slots in DATA
  // Map TES4 skills to TES5 skills
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportREFR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'REFR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // NAME (base object) needs FormID resolution
  // For now, create record shell
  
  // Position/Rotation
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Position\X', RecordValueFloat('PosX'));
  SetElementNativeValues(rec, 'DATA\Position\Y', RecordValueFloat('PosY'));
  SetElementNativeValues(rec, 'DATA\Position\Z', RecordValueFloat('PosZ'));
  SetElementNativeValues(rec, 'DATA\Rotation\X', RecordValueFloat('RotX'));
  SetElementNativeValues(rec, 'DATA\Rotation\Y', RecordValueFloat('RotY'));
  SetElementNativeValues(rec, 'DATA\Rotation\Z', RecordValueFloat('RotZ'));
  
  // Scale
  if RecordHasKey('XSCL') then begin
    Add(rec, 'XSCL', True);
    SetElementNativeValues(rec, 'XSCL', RecordValueFloat('XSCL'));
  end;
  
  // Lock
  if RecordHasKey('XLOC.Level') then begin
    Add(rec, 'XLOC', True);
    SetElementNativeValues(rec, 'XLOC\Level', RecordValueInt('XLOC.Level'));
    SetElementNativeValues(rec, 'XLOC\Flags', RecordValueInt('XLOC.Flags'));
  end;
  
  // Enable parent
  if RecordHasKey('XESP.Ref') then begin
    Add(rec, 'XESP', True);
    // Reference needs FormID resolution
    SetElementNativeValues(rec, 'XESP\Flags', RecordValueInt('XESP.Opposite'));
  end;
  
  // Count
  if RecordHasKey('XCNT') then begin
    Add(rec, 'XCNT', True);
    SetElementNativeValues(rec, 'XCNT', RecordValueInt('XCNT'));
  end;
  
  // Map Marker
  if RecordHasKey('HasMapMarker') then begin
    Add(rec, 'Map Marker', True);
    Add(rec, 'XMRK', True);
    // TES5 map marker uses TNAM with different type enum
    // TES4 types: 0=None,1=Camp,2=Cave,...
    // TES5 types have different indices - requires mapping
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportACHR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'ACHR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // NAME base needs FormID resolution
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Position\X', RecordValueFloat('PosX'));
  SetElementNativeValues(rec, 'DATA\Position\Y', RecordValueFloat('PosY'));
  SetElementNativeValues(rec, 'DATA\Position\Z', RecordValueFloat('PosZ'));
  SetElementNativeValues(rec, 'DATA\Rotation\X', RecordValueFloat('RotX'));
  SetElementNativeValues(rec, 'DATA\Rotation\Y', RecordValueFloat('RotY'));
  SetElementNativeValues(rec, 'DATA\Rotation\Z', RecordValueFloat('RotZ'));
  
  if RecordHasKey('XSCL') then begin
    Add(rec, 'XSCL', True);
    SetElementNativeValues(rec, 'XSCL', RecordValueFloat('XSCL'));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportREGN();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'REGN');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // RCLR map color
  if RecordHasKey('RCLR') then begin
    Add(rec, 'RCLR', True);
    SetElementEditValues(rec, 'RCLR', RecordValue('RCLR'));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportSCRL();
// SGST -> SCRL (Sigil Stone -> Scroll)
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'SCRL');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportSLGM();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'SLGM');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  Add(rec, 'SOUL', True);
  SetElementNativeValues(rec, 'SOUL', RecordValueInt('SOUL'));
  
  Add(rec, 'SLCP', True);
  SetElementNativeValues(rec, 'SLCP', RecordValueInt('SLCP'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportSOUN();
var
  rec: IInterface;
begin
  // TES5 splits SOUN into SOUN + SNDR (Sound Descriptor)
  // Create a basic SOUN
  rec := CreateNewRecord(TargetPlugin, 'SOUN');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // TES5 SOUN is minimal - real data is in SNDR
  // We'd also need to create a SNDR record
  // For simplicity, store filename as note
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportSPEL();
var
  rec: IInterface;
  tes4Type, tes5Type: Integer;
  tes4Level: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'SPEL');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  // SPIT -> TES5 SPIT has different structure
  // TES4 types: 0=Spell,1=Disease,2=Power,3=LesserPower,4=Ability,5=Poison
  // TES5 types: 0=Spell,1=Disease,2=Power,3=LesserPower,4=Ability,5=Poison,
  //             10=Addiction,11=Voice
  tes4Type := RecordValueInt('SPIT.Type');
  tes5Type := tes4Type; // Direct mapping for 0-5
  
  Add(rec, 'SPIT', True);
  SetElementNativeValues(rec, 'SPIT\Type', tes5Type);
  SetElementNativeValues(rec, 'SPIT\Cost', RecordValueInt('SPIT.Cost'));
  
  // TES4 Level: 0=Novice,1=Apprentice,2=Journeyman,3=Expert,4=Master
  // TES5 has Half Cost Perk instead of spell level
  tes4Level := RecordValueInt('SPIT.Level');
  // Map to cast duration/charge time instead - approximation
  
  SetElementNativeValues(rec, 'SPIT\Flags', RecordValueInt('SPIT.Flags'));
  
  // Effects need MGEF resolution
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportSTAT();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'STAT');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportTREE();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'TREE');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetModel(rec, RecordValue('SPTFile.MODL'));
  
  // TES5 TREE CNAM has different fields
  Add(rec, 'CNAM', True);
  SetElementNativeValues(rec, 'CNAM\Trunk Flexibility', RecordValueFloat('CNAM.RockSpeed'));
  SetElementNativeValues(rec, 'CNAM\Branch Flexibility', RecordValueFloat('CNAM.RustleSpeed'));
  SetElementNativeValues(rec, 'CNAM\Leaf Amplitude', RecordValueFloat('CNAM.LeafCurve'));
  SetElementNativeValues(rec, 'CNAM\Leaf Frequency', RecordValueFloat('CNAM.LeafDim'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportWATR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'WATR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  Add(rec, 'ANAM', True);
  SetElementNativeValues(rec, 'ANAM', RecordValueInt('ANAM.Opacity'));
  
  // TES5 WATR DATA/DNAM structure is very different
  // Basic properties that exist in both
  Add(rec, 'DNAM', True);
  SetElementNativeValues(rec, 'DNAM\Wind Velocity', RecordValueFloat('DATA.WindVelocity'));
  SetElementNativeValues(rec, 'DNAM\Wind Direction', RecordValueFloat('DATA.WindDirection'));
  SetElementNativeValues(rec, 'DNAM\Fresnel Amount', RecordValueFloat('DATA.Fresnel'));
  SetElementNativeValues(rec, 'DNAM\Damage', RecordValueInt('DATA.Damage'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportWEAP();
var
  rec: IInterface;
  animType: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'WEAP');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  // DNAM (weapon data in TES5)
  Add(rec, 'DNAM', True);
  animType := RecordValueInt('TES5.AnimType');
  SetElementNativeValues(rec, 'DNAM\Animation Type', animType);
  SetElementNativeValues(rec, 'DNAM\Speed', RecordValueFloat('DATA.Speed'));
  SetElementNativeValues(rec, 'DNAM\Reach', RecordValueFloat('DATA.Reach'));
  SetElementNativeValues(rec, 'DNAM\Stagger', 0); // No TES4 equivalent
  
  // DATA (value/weight in TES5)
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  SetElementNativeValues(rec, 'DATA\Damage', RecordValueInt('DATA.Damage'));
  
  // Critical data
  Add(rec, 'CRDT', True);
  SetElementNativeValues(rec, 'CRDT\Damage', RecordValueInt('DATA.Damage') div 2);
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportWTHR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'WTHR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // Cloud textures
  // TES5 has different cloud layer system
  if RecordValue('CNAM.CloudLower') <> '' then begin
    // TES5 uses 00TX-3FTX for cloud textures
    // For simplicity, map lower layer cloud
  end;
  
  // DATA
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Wind Speed', RecordValueInt('DATA.WindSpeed'));
  SetElementNativeValues(rec, 'DATA\Trans Delta', RecordValueInt('DATA.TransDelta'));
  SetElementNativeValues(rec, 'DATA\Sun Glare', RecordValueInt('DATA.SunGlare'));
  SetElementNativeValues(rec, 'DATA\Sun Damage', RecordValueInt('DATA.SunDamage'));
  
  // HDR data
  if RecordHasKey('HNAM.EyeAdaptSpeed') then begin
    Add(rec, 'HNAM', True);
    SetElementNativeValues(rec, 'HNAM\Eye Adapt Speed', RecordValueFloat('HNAM.EyeAdaptSpeed'));
    SetElementNativeValues(rec, 'HNAM\Bloom Blur Radius', RecordValueFloat('HNAM.BlurRadius'));
    SetElementNativeValues(rec, 'HNAM\Bloom Threshold', RecordValueFloat('HNAM.EmissiveMult'));
    SetElementNativeValues(rec, 'HNAM\Bloom Scale', RecordValueFloat('HNAM.BrightScale'));
    SetElementNativeValues(rec, 'HNAM\Sunlight Dimmer', RecordValueFloat('HNAM.BrightClamp'));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

procedure ImportWRLD();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'WRLD');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Flags'));
  
  // Parent worldspace, climate, water need FormID resolution
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
end;

//============================================================================
// Main Import Dispatcher
//============================================================================

procedure ProcessImportRecord();
var
  targetType, note: string;
begin
  targetType := RecordValue('TargetType');
  
  if targetType = '' then Exit;
  
  // Skip reference/info types
  if targetType = 'PGRD_SKIP' then begin Inc(SkippedCount); Exit; end;
  if targetType = 'ROAD_SKIP' then begin Inc(SkippedCount); Exit; end;
  if targetType = 'SCPT_SOURCE' then begin Inc(SkippedCount); Exit; end;
  if targetType = 'SKIL_REF' then begin Inc(SkippedCount); Exit; end;
  if targetType = 'BSGN_SPELLS' then begin Inc(SkippedCount); Exit; end;
  if targetType = 'UNKNOWN' then begin Inc(SkippedCount); Exit; end;
  if targetType = 'LAND' then begin Inc(SkippedCount); Exit; end;
  if targetType = 'INFO' then begin Inc(SkippedCount); Exit; end;
  
  // Dispatch to appropriate importer
  if targetType = 'ACTI' then ImportACTI()
  else if targetType = 'ALCH' then ImportALCH()
  else if targetType = 'AMMO' then ImportAMMO()
  else if targetType = 'ARMO' then ImportARMO()
  else if targetType = 'BOOK' then ImportBOOK()
  else if targetType = 'CELL' then ImportCELL()
  else if targetType = 'CLAS' then ImportCLAS()
  else if targetType = 'CLMT' then ImportCLMT()
  else if targetType = 'CONT' then ImportCONT()
  else if targetType = 'NPC_' then ImportNPC()
  else if targetType = 'CSTY' then begin Inc(SkippedCount); end  // CSTY very different, skip
  else if targetType = 'DIAL' then ImportDIAL()
  else if targetType = 'DOOR' then ImportDOOR()
  else if targetType = 'EFSH' then ImportEFSH()
  else if targetType = 'ENCH' then ImportENCH()
  else if targetType = 'EYES' then ImportEYES()
  else if targetType = 'FACT' then ImportFACT()
  else if targetType = 'FLOR' then ImportFLOR()
  else if targetType = 'FURN' then ImportFURN()
  else if targetType = 'GLOB' then ImportGLOB()
  else if targetType = 'GMST' then ImportGMST()
  else if targetType = 'GRAS' then ImportGRAS()
  else if targetType = 'HDPT' then ImportHDPT()
  else if targetType = 'IDLE' then ImportIDLE()
  else if targetType = 'INGR' then ImportINGR()
  else if targetType = 'KEYM' then ImportKEYM()
  else if targetType = 'LIGH' then ImportLIGH()
  else if targetType = 'LSCR' then ImportLSCR()
  else if targetType = 'LTEX' then ImportLTEX()
  else if targetType = 'LVLN' then ImportLVLN()
  else if targetType = 'LVLI' then ImportLVLI()
  else if targetType = 'LVSP' then ImportLVSP()
  else if targetType = 'MGEF' then ImportMGEF()
  else if targetType = 'MISC' then ImportMISC()
  else if targetType = 'PACK' then ImportPACK()
  else if targetType = 'QUST' then ImportQUST()
  else if targetType = 'RACE' then ImportRACE()
  else if targetType = 'REFR' then ImportREFR()
  else if targetType = 'ACHR' then ImportACHR()
  else if targetType = 'REGN' then ImportREGN()
  else if targetType = 'SCRL' then ImportSCRL()
  else if targetType = 'SLGM' then ImportSLGM()
  else if targetType = 'SOUN' then ImportSOUN()
  else if targetType = 'SPEL' then ImportSPEL()
  else if targetType = 'STAT' then ImportSTAT()
  else if targetType = 'TREE' then ImportTREE()
  else if targetType = 'WATR' then ImportWATR()
  else if targetType = 'WEAP' then ImportWEAP()
  else if targetType = 'WTHR' then ImportWTHR()
  else if targetType = 'WRLD' then ImportWRLD()
  else if targetType = 'ANIO' then ImportSTAT() // ANIO skeletal - keep as static
  else begin
    AddMessage('  Unknown target type: ' + targetType + ' (EditorID: ' + RecordValue('EditorID') + ')');
    Inc(SkippedCount);
    Exit;
  end;
  
  Inc(RecordCount);
  if RecordCount mod 500 = 0 then
    AddMessage('  Imported ' + IntToStr(RecordCount) + ' records...');
end;

//============================================================================
// Entry Points
//============================================================================

function Initialize: Integer;
var
  importFile: string;
  i: Integer;
  ErrorCount: Integer;
begin
  Result := 0;
  ImportPath := DataPath + 'TES4Export\';
  importFile := ImportPath + 'TES4_Records.txt';
  
  if not FileExists(importFile) then begin
    AddMessage('ERROR: Import file not found: ' + importFile);
    AddMessage('Run TES4_Export_Records.pas in TES4Edit first.');
    Result := 1;
    Exit;
  end;
  
  // Get target plugin: pick the first writable .esp (skip ESMs and .exe)
  TargetPlugin := nil;
  for i := 0 to FileCount - 1 do begin
    if not GetIsESM(FileByIndex(i)) then begin
      if Pos('.exe', LowerCase(GetFileName(FileByIndex(i)))) = 0 then begin
        TargetPlugin := FileByIndex(i);
        Break;
      end;
    end;
  end;
  
  if not Assigned(TargetPlugin) then begin
    AddMessage('ERROR: No target .esp plugin found. Create a new plugin first.');
    Result := 1;
    Exit;
  end;
  
  AddMessage('TES5 Record Import: Starting...');
  AddMessage('Import file: ' + importFile);
  AddMessage('Target plugin: ' + GetFileName(TargetPlugin));
  
  slImport := TStringList.Create;
  slImport.LoadFromFile(importFile);
  
  slFormIDMap := TStringList.Create;
  
  recData := TStringList.Create;
  
  RecordCount := 0;
  SkippedCount := 0;
  CurrentLine := 0;
  ErrorCount := 0;
  
  while ReadNextRecord() do begin
      ProcessImportRecord();
  end;
  recData.Free;
  
  // Save FormID mapping for cross-reference fixup
  slFormIDMap.SaveToFile(ImportPath + 'FormID_Mapping.txt');
  
  AddMessage('');
  AddMessage('TES5 Record Import: Complete!');
  AddMessage('Records imported: ' + IntToStr(RecordCount));
  AddMessage('Records skipped: ' + IntToStr(SkippedCount));
  if ErrorCount > 0 then
    AddMessage('Records failed with errors: ' + IntToStr(ErrorCount));
  AddMessage('FormID mapping saved to: ' + ImportPath + 'FormID_Mapping.txt');
  AddMessage('');
  AddMessage('=== POST-IMPORT TASKS ===');
  AddMessage('1. Cross-references (FormIDs) need manual relinking using FormID_Mapping.txt');
  AddMessage('2. NIF meshes must be converted from Oblivion to Skyrim format');
  AddMessage('3. DDS textures may need recompression (DXT1/DXT5 -> BC formats for SSE)');
  AddMessage('4. TES4 scripts (SCPT) must be rewritten in Papyrus');
  AddMessage('5. Magic effects need MGEF FormID resolution');
  AddMessage('6. Leveled list entries need FormID relinking');
  AddMessage('7. NPC items, spells, factions, and packages need FormID relinking');
  AddMessage('8. Path Grids (PGRD) must be rebuilt as NavMesh (NAVM) in the CK');
  AddMessage('9. LAND heightmap data requires manual conversion');
  AddMessage('10. Quest stages and dialog may need structural adjustments');
  AddMessage('11. BSGN (Birthsign) spells should be added to Race records or Standing Stones');
  AddMessage('12. Packages (PACK) use a completely different system in TES5 - manual rebuild needed');
  
  slImport.Free;
  slFormIDMap.Free;
end;

function Process(e: IInterface): Integer;
begin
  // Not used - all processing done in Initialize
  Result := 0;
end;

function Finalize: Integer;
begin
  Result := 0;
end;

end.

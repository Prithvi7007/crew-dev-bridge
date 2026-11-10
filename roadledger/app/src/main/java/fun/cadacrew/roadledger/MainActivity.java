package fun.cadacrew.roadledger;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;
import android.text.InputType;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

public final class MainActivity extends Activity {
    private static final int EXPORT_REQUEST=1042;
    private static final String[] TABS={"Overview","Fill-ups","Expenses","Trips","Garage"};
    private GarageDb db;
    private long selectedVehicle=-1;
    private int tab=0;
    private LinearLayout body;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        db=new GarageDb(this);
        getWindow().setStatusBarColor(Ui.BG);
        getWindow().setNavigationBarColor(Ui.BG);
        render();
    }
    @Override protected void onDestroy() {
        if(db!=null) db.close();
        super.onDestroy();
    }
    private GarageDb.Vehicle active() {
        List<GarageDb.Vehicle> v=db.vehicles();
        for(GarageDb.Vehicle x:v) if(x.id==selectedVehicle) return x;
        selectedVehicle=v.isEmpty()?-1:v.get(0).id;
        return v.isEmpty()?null:v.get(0);
    }
    private void render() {
        GarageDb.Vehicle vehicle=active();
        LinearLayout shell=Ui.col(this);
        shell.setBackgroundColor(Ui.BG);
        shell.setFitsSystemWindows(true);

        LinearLayout header=Ui.col(this);
        header.setPadding(Ui.dp(this,22),Ui.dp(this,22),Ui.dp(this,22),Ui.dp(this,11));
        LinearLayout brand=Ui.row(this);
        TextView name=Ui.label(this,"ROADLEDGER",25,Ui.INK,true);
        name.setLetterSpacing(.055f);
        brand.addView(name,new LinearLayout.LayoutParams(0,-2,1));
        TextView badge=Ui.label(this,"●  OFFLINE",11,Ui.MINT,true);
        badge.setPadding(Ui.dp(this,10),Ui.dp(this,8),Ui.dp(this,10),Ui.dp(this,8));
        badge.setBackground(Ui.shape(Ui.CARD_ALT,this,12));
        brand.addView(badge);
        header.addView(brand);
        header.addView(Ui.label(this,"Your vehicle. Every detail.",13,Ui.MUTED,false),Ui.below(this,2));
        TextView selector=Ui.label(this,vehicle==null?"＋ Add your first vehicle": "▾  "+vehicle.name+"   ·   "+join(vehicle.make,vehicle.model),13,Ui.INK,true);
        selector.setBackground(Ui.shape(Ui.CARD_ALT,this,13));
        selector.setPadding(Ui.dp(this,16),Ui.dp(this,12),Ui.dp(this,16),Ui.dp(this,12));
        selector.setOnClickListener(v->chooseVehicle());
        header.addView(selector,Ui.below(this,18));
        shell.addView(header);

        ScrollView scroll=new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setVerticalScrollBarEnabled(false);
        body=Ui.col(this);
        body.setPadding(Ui.dp(this,20),Ui.dp(this,9),Ui.dp(this,20),Ui.dp(this,30));
        scroll.addView(body);
        shell.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        if(vehicle==null) emptyScreen();
        else if(tab==0) overview(vehicle);
        else if(tab==1) fuelScreen(vehicle);
        else if(tab==2) expenseScreen(vehicle);
        else if(tab==3) tripScreen(vehicle);
        else garageScreen(vehicle);
        bottomNavigation(shell);
        setContentView(shell);
    }
    private void bottomNavigation(LinearLayout shell) {
        LinearLayout nav=Ui.row(this);
        nav.setPadding(Ui.dp(this,7),Ui.dp(this,10),Ui.dp(this,7),Ui.dp(this,12));
        nav.setBackgroundColor(Ui.CARD);
        String[] icons={"◉","◈","▣","⌁","▦"};
        for(int i=0;i<TABS.length;i++){
            final int target=i;
            LinearLayout cell=Ui.col(this);
            cell.setGravity(Gravity.CENTER);
            TextView icon=Ui.label(this,icons[i],22,tab==i?Ui.MINT:Ui.MUTED,true);
            icon.setGravity(Gravity.CENTER);
            TextView label=Ui.label(this,TABS[i],10,tab==i?Ui.MINT:Ui.MUTED,tab==i);
            label.setGravity(Gravity.CENTER);
            cell.addView(icon);
            cell.addView(label);
            cell.setPadding(0,Ui.dp(this,3),0,Ui.dp(this,3));
            if(tab==i) cell.setBackground(Ui.shape(Ui.CARD_ALT,this,14));
            cell.setOnClickListener(v->{tab=target;render();});
            nav.addView(cell,new LinearLayout.LayoutParams(0,Ui.dp(this,59),1));
        }
        shell.addView(nav);
    }
    private void emptyScreen(){
        section("Welcome to RoadLedger","A simple, private way to understand your vehicle.");
        LinearLayout card=Ui.card(this);
        card.addView(Ui.label(this,"Start with your vehicle",22,Ui.INK,true));
        card.addView(Ui.label(this,"Add one vehicle, then log fill-ups and expenses. Your data stays on your phone.",14,Ui.MUTED,false),Ui.below(this,10));
        android.widget.Button b=Ui.primary(this,"＋  Add a vehicle");
        b.setOnClickListener(v->vehicleDialog());
        card.addView(b,Ui.below(this,22));
        body.addView(card,Ui.below(this,15));
        note("NO ACCOUNT  ·  NO ADS  ·  NO TRACKING");
    }
    private void overview(GarageDb.Vehicle v){
        section("Your dashboard","Everything important at a glance");
        List<GarageDb.Fuel> fuel=db.fuel(v.id);
        List<GarageDb.Expense> costs=db.expenses(v.id);
        List<GarageDb.Trip> trips=db.trips(v.id);
        GarageDb.Economy economy=db.economy(v.id);
        double fuelSpend=0,maintenance=0,totalTripKm=0;
        for(GarageDb.Fuel f:fuel) fuelSpend+=f.cost;
        for(GarageDb.Expense e:costs) maintenance+=e.amount;
        for(GarageDb.Trip t:trips) totalTripKm+=t.endKm-t.startKm;

        LinearLayout r1=Ui.row(this);
        r1.addView(metric("AVERAGE ECONOMY",Double.isNaN(economy.litersPer100Km)?"—":fmt(economy.litersPer100Km),"L / 100 km",Ui.MINT),weighted());
        r1.addView(metric("FUEL SPEND",usd(fuelSpend),fuel.size()+" fill-ups",Ui.BLUE),weighted(12));
        body.addView(r1,Ui.below(this,18));
        LinearLayout r2=Ui.row(this);
        r2.addView(metric("OTHER EXPENSES",usd(maintenance),costs.size()+" entries",Ui.ORANGE),weighted());
        r2.addView(metric("TRIP DISTANCE",fmt(totalTripKm)+" km",trips.size()+" manual trips",Ui.MINT),weighted(12));
        body.addView(r2,Ui.below(this,12));

        LinearLayout chart=Ui.card(this);
        chart.addView(Ui.label(this,"Fuel economy trend",19,Ui.INK,true));
        chart.addView(Ui.label(this,"Full-to-full fuel economy · L/100 km",12,Ui.MUTED,false),Ui.below(this,4));
        chart.addView(new Ui.EconomyChart(this,economy.intervalValues),Ui.below(this,12));
        if(economy.intervalValues.isEmpty()) chart.addView(Ui.label(this,"Record two full tanks to calculate meaningful consumption.",12,Ui.MUTED,false));
        else chart.addView(Ui.label(this,fmt(economy.totalDistanceKm)+" km  ·  "+fmt(economy.totalLiters)+" L in completed intervals",12,Ui.MUTED,false));
        body.addView(chart,Ui.below(this,18));

        LinearLayout actions=Ui.row(this);
        android.widget.Button fuelButton=Ui.primary(this,"＋  Fill-up");
        fuelButton.setOnClickListener(w->fuelDialog(v));
        actions.addView(fuelButton,weighted());
        android.widget.Button costButton=Ui.secondary(this,"＋  Expense");
        costButton.setOnClickListener(w->expenseDialog(v));
        actions.addView(costButton,weighted(12));
        body.addView(actions,Ui.below(this,18));

        if(!fuel.isEmpty()){
            body.addView(Ui.label(this,"RECENT ACTIVITY",12,Ui.MUTED,true),Ui.below(this,25));
            int count=Math.min(3,fuel.size());
            for(int i=0;i<count;i++) fuelRecord(fuel.get(i));
        }
        android.widget.Button export=Ui.secondary(this,"↓  Export all your data as CSV");
        export.setOnClickListener(w->exportData());
        body.addView(export,Ui.below(this,25));
        note("All numbers are for the selected vehicle. Amounts in USD.");
    }
    private void fuelScreen(GarageDb.Vehicle v){
        section("Fill-ups","Track every tank, full or partial.");
        addButton("＋  New fill-up",()->fuelDialog(v));
        GarageDb.Economy e=db.economy(v.id);
        summaryNote(Double.isNaN(e.litersPer100Km)?"Fuel economy pending":fmt(e.litersPer100Km)+" L / 100 km",
            e.intervalValues.isEmpty()?"Add two full-tank fill-ups with increasing odometer readings.":"Weighted across "+e.intervalValues.size()+" completed full-to-full intervals.");
        List<GarageDb.Fuel> entries=db.fuel(v.id);
        if(entries.isEmpty()) noRecords("No fill-ups yet","Your first entry is one tap away.");
        for(GarageDb.Fuel f:entries) fuelRecord(f);
        note("Long-press a fill-up to delete it.");
    }
    private void fuelRecord(GarageDb.Fuel f) {
        LinearLayout c=record("⛽  "+fmt(f.liters)+" L  ·  "+usd(f.cost),
            day(f.date)+"   ·   "+fmt(f.odometer)+" km   ·   "+(f.full?"Full tank":"Partial fill-up"));
        c.setOnLongClickListener(v->{confirm("Delete fill-up?","This record will be permanently removed.",()->db.deleteFuel(f.id));return true;});
        body.addView(c,Ui.below(this,10));
    }
    private void expenseScreen(GarageDb.Vehicle v){
        section("Expenses","Maintenance, repairs and costs in one place.");
        addButton("＋  New expense",()->expenseDialog(v));
        List<GarageDb.Expense> entries=db.expenses(v.id);
        double sum=0;for(GarageDb.Expense e:entries) sum+=e.amount;
        summaryNote(usd(sum)+" total","Across "+entries.size()+" non-fuel expenses.");
        if(entries.isEmpty()) noRecords("No expenses yet","Log a service, repair, parking charge or any other cost.");
        for(GarageDb.Expense e:entries) {
            LinearLayout c=record(e.category+"  ·  "+usd(e.amount),
                day(e.date)+"   ·   "+(e.odometer>0?fmt(e.odometer)+" km   ·   ":"")+e.note);
            c.setOnLongClickListener(w->{confirm("Delete expense?","This record will be permanently removed.",()->db.deleteExpense(e.id));return true;});
            body.addView(c,Ui.below(this,10));
        }
        note("Long-press an expense to delete it.");
    }
    private void tripScreen(GarageDb.Vehicle v){
        section("Trip log","Manual odometer-based trips, no GPS permission.");
        addButton("＋  Record a trip",()->tripDialog(v));
        List<GarageDb.Trip> entries=db.trips(v.id);
        double total=0;for(GarageDb.Trip t:entries) total+=t.endKm-t.startKm;
        summaryNote(fmt(total)+" km logged",entries.size()+" manually recorded trips.");
        if(entries.isEmpty()) noRecords("No trips yet","Enter start and end odometer readings to record distance.");
        for(GarageDb.Trip t:entries) {
            LinearLayout c=record("↗  "+fmt(t.endKm-t.startKm)+" km",day(t.date)+"   ·   "+fmt(t.startKm)+" → "+fmt(t.endKm)+" km"+(t.note.isEmpty()?"":"   ·   "+t.note));
            c.setOnLongClickListener(w->{confirm("Delete trip?","This record will be permanently removed.",()->db.deleteTrip(t.id));return true;});
            body.addView(c,Ui.below(this,10));
        }
        note("This version does not collect or store GPS coordinates.");
    }
    private void garageScreen(GarageDb.Vehicle selected){
        section("Your garage","Every vehicle in one private place.");
        addButton("＋  Add another vehicle",this::vehicleDialog);
        for(GarageDb.Vehicle v:db.vehicles()) {
            LinearLayout card=record((v.id==selectedVehicle?"●  ":"○  ")+v.name,join(v.make,v.model)+"  ·  tap to select");
            card.setOnClickListener(w->{selectedVehicle=v.id;render();});
            card.setOnLongClickListener(w->{
                confirm("Delete "+v.name+"?","This permanently deletes the vehicle AND its fill-ups, expenses and trips.",()->{
                    db.deleteVehicle(v.id);
                    if(selectedVehicle==v.id)selectedVehicle=-1;
                });return true;
            });
            body.addView(card,Ui.below(this,12));
        }
        android.widget.Button export=Ui.secondary(this,"↓  Export your records (CSV)");
        export.setOnClickListener(w->exportData());
        body.addView(export,Ui.below(this,25));
        note("Long-press a vehicle to delete it and all of its data.");
    }

    private void section(String title,String subtitle) {
        body.addView(Ui.label(this,title,27,Ui.INK,true));
        body.addView(Ui.label(this,subtitle,13,Ui.MUTED,false),Ui.below(this,5));
    }
    private void note(String text) {
        TextView t=Ui.label(this,text,11,Ui.MUTED,false);
        t.setGravity(Gravity.CENTER);body.addView(t,Ui.below(this,21));
    }
    private void noRecords(String title,String explanation) {
        LinearLayout card=Ui.card(this);
        card.addView(Ui.label(this,title,17,Ui.INK,true));
        card.addView(Ui.label(this,explanation,13,Ui.MUTED,false),Ui.below(this,6));
        body.addView(card,Ui.below(this,16));
    }
    private void summaryNote(String title,String explanation) {
        LinearLayout card=Ui.card(this);
        card.setBackground(Ui.shape(Ui.CARD_ALT,this,21));
        card.addView(Ui.label(this,title,21,Ui.MINT,true));
        card.addView(Ui.label(this,explanation,12,Ui.MUTED,false),Ui.below(this,4));
        body.addView(card,Ui.below(this,18));
    }
    private LinearLayout metric(String heading,String value,String detail,int color) {
        LinearLayout c=Ui.card(this);
        c.setPadding(Ui.dp(this,13),Ui.dp(this,18),Ui.dp(this,10),Ui.dp(this,18));
        c.addView(Ui.label(this,heading,10,Ui.MUTED,true));
        c.addView(Ui.label(this,value,22,color,true),Ui.below(this,9));
        c.addView(Ui.label(this,detail,11,Ui.MUTED,false),Ui.below(this,5));
        return c;
    }
    private LinearLayout.LayoutParams weighted() {return new LinearLayout.LayoutParams(0,-2,1);}
    private LinearLayout.LayoutParams weighted(int leftMargin) {
        LinearLayout.LayoutParams p=weighted();p.leftMargin=Ui.dp(this,leftMargin);return p;
    }
    private LinearLayout record(String headline,String sub) {
        LinearLayout c=Ui.card(this);
        c.addView(Ui.label(this,headline,16,Ui.INK,true));
        c.addView(Ui.label(this,sub,12,Ui.MUTED,false),Ui.below(this,8));
        return c;
    }
    private void addButton(String title,Runnable r) {
        android.widget.Button b=Ui.primary(this,title);
        b.setOnClickListener(v->r.run());
        body.addView(b,Ui.below(this,18));
    }
    private void chooseVehicle(){
        List<GarageDb.Vehicle> vehicles=db.vehicles();
        if(vehicles.isEmpty()) {vehicleDialog();return;}
        String[] items=new String[vehicles.size()+1];
        for(int i=0;i<vehicles.size();i++)items[i]=vehicles.get(i).name+"  ·  "+join(vehicles.get(i).make,vehicles.get(i).model);
        items[vehicles.size()]="+ Add another vehicle";
        new AlertDialog.Builder(this).setTitle("Select vehicle").setItems(items,(dialog,which)->{
            if(which==vehicles.size())vehicleDialog();
            else{selectedVehicle=vehicles.get(which).id;render();}
        }).show();
    }
    private interface SaveAction {void save() throws Exception;}
    private void form(String title,LinearLayout fields,SaveAction action) {
        LinearLayout wrap=Ui.col(this);
        wrap.setPadding(Ui.dp(this,20),Ui.dp(this,8),Ui.dp(this,20),0);
        wrap.addView(fields);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle(title).setView(wrap)
            .setPositiveButton("Save",null).setNegativeButton("Cancel",(d,w)->{}).create();
        dialog.setOnShowListener(ignored->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{
            try {action.save();dialog.dismiss();render();}
            catch(Exception ex){toast(ex instanceof NumberFormatException?"Enter valid numeric values.":(ex.getMessage()==null?"Invalid input.":ex.getMessage()));}
        }));
        dialog.show();
    }
    private EditText field(LinearLayout parent,String label,String placeholder,boolean number) {
        TextView t=Ui.label(this,label,13,Color.DKGRAY,true);
        parent.addView(t,Ui.below(this,12));
        EditText e=new EditText(this);
        e.setSingleLine(true);e.setTextSize(16);e.setHint(placeholder);
        e.setTextColor(Color.BLACK);e.setHintTextColor(Color.GRAY);
        if(number) e.setInputType(InputType.TYPE_CLASS_NUMBER|InputType.TYPE_NUMBER_FLAG_DECIMAL|InputType.TYPE_NUMBER_FLAG_SIGNED);
        else e.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        parent.addView(e,new LinearLayout.LayoutParams(-1,Ui.dp(this,50)));
        return e;
    }
    private static double val(EditText e,String field) {
        String text=e.getText().toString().trim();
        if(text.isEmpty()) throw new IllegalArgumentException("Enter "+field+".");
        double n=Double.parseDouble(text);
        if(!Double.isFinite(n))throw new IllegalArgumentException("Invalid "+field+".");
        return n;
    }
    private void vehicleDialog() {
        LinearLayout col=Ui.col(this);
        EditText name=field(col,"VEHICLE NAME","e.g. My Corolla",false);
        EditText make=field(col,"MAKE","Toyota (optional)",false);
        EditText model=field(col,"MODEL","Corolla (optional)",false);
        form("Add vehicle",col,()->{
            String label=name.getText().toString().trim();
            if(label.isEmpty())throw new IllegalArgumentException("Vehicle name is required.");
            selectedVehicle=db.addVehicle(label,make.getText().toString(),model.getText().toString());
            tab=0;
        });
    }
    private void fuelDialog(GarageDb.Vehicle vehicle) {
        LinearLayout col=Ui.col(this);
        EditText odo=field(col,"ODOMETER (KM)","e.g. 60520",true);
        EditText liters=field(col,"FUEL PURCHASED (LITERS)","e.g. 32.4",true);
        EditText cost=field(col,"TOTAL COST (USD)","e.g. 47.50",true);
        CheckBox full=new CheckBox(this);
        full.setText("Filled tank completely");full.setChecked(true);full.setTextColor(Color.DKGRAY);
        col.addView(full,Ui.below(this,15));
        form("New fill-up",col,()->{
            db.addFuel(vehicle.id,System.currentTimeMillis(),val(odo,"odometer"),val(liters,"liters"),val(cost,"cost"),full.isChecked());
        });
    }
    private void expenseDialog(GarageDb.Vehicle vehicle) {
        LinearLayout col=Ui.col(this);
        col.addView(Ui.label(this,"CATEGORY",13,Color.DKGRAY,true),Ui.below(this,12));
        Spinner category=new Spinner(this);
        String[] options={"Maintenance","Repairs","Insurance","Parking","Tolls","Registration","Other"};
        ArrayAdapter<String> adapter=new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,options);
        category.setAdapter(adapter);col.addView(category,Ui.below(this,4));
        EditText amount=field(col,"AMOUNT (USD)","e.g. 89.50",true);
        EditText odo=field(col,"ODOMETER (KM, OPTIONAL)","0",true);
        EditText note=field(col,"NOTES","What was this for?",false);
        form("New expense",col,()->{
            String km=odo.getText().toString().trim();
            db.addExpense(vehicle.id,System.currentTimeMillis(),options[category.getSelectedItemPosition()],val(amount,"amount"),km.isEmpty()?0:Double.parseDouble(km),note.getText().toString());
        });
    }
    private void tripDialog(GarageDb.Vehicle vehicle) {
        LinearLayout col=Ui.col(this);
        EditText start=field(col,"START ODOMETER (KM)","e.g. 60700",true);
        EditText end=field(col,"END ODOMETER (KM)","e.g. 60735",true);
        EditText note=field(col,"TRIP PURPOSE / NOTES","e.g. Commute",false);
        form("Record manual trip",col,()->{
            db.addTrip(vehicle.id,System.currentTimeMillis(),val(start,"starting odometer"),val(end,"ending odometer"),note.getText().toString());
        });
    }
    private void confirm(String title,String message,Runnable action) {
        new AlertDialog.Builder(this).setTitle(title).setMessage(message)
            .setPositiveButton("Delete",(d,w)->{action.run();render();})
            .setNegativeButton("Cancel",null).show();
    }
    private void exportData(){
        Intent i=new Intent(Intent.ACTION_CREATE_DOCUMENT);
        i.addCategory(Intent.CATEGORY_OPENABLE);
        i.setType("text/csv");
        i.putExtra(Intent.EXTRA_TITLE,"roadledger-export.csv");
        try{startActivityForResult(i,EXPORT_REQUEST);}
        catch(Exception e){toast("No document picker found.");}
    }
    @Override protected void onActivityResult(int requestCode,int resultCode,Intent data){
        super.onActivityResult(requestCode,resultCode,data);
        if(requestCode!=EXPORT_REQUEST||resultCode!=RESULT_OK||data==null||data.getData()==null)return;
        try(OutputStream out=getContentResolver().openOutputStream(data.getData())){
            if(out==null)throw new IllegalStateException("Could not open export destination.");
            out.write(buildCsv().getBytes(StandardCharsets.UTF_8));
            toast("CSV exported successfully.");
        }catch(Exception e){toast("Export failed: "+e.getMessage());}
    }
    private String buildCsv(){
        StringBuilder b=new StringBuilder();
        b.append("record_type,vehicle_id,vehicle_name,date,odometer_start_km,odometer_end_km,liters,amount_usd,full_tank,category,note\n");
        for(GarageDb.Vehicle v:db.vehicles()){
            csv(b,"vehicle",v.id,v.name,"","","","","","","",join(v.make,v.model));
            for(GarageDb.Fuel f:db.fuel(v.id))
                csv(b,"fuel",v.id,v.name,iso(f.date),f.odometer,"",f.liters,f.cost,f.full?"yes":"no","","");
            for(GarageDb.Expense e:db.expenses(v.id))
                csv(b,"expense",v.id,v.name,iso(e.date),e.odometer,"","",e.amount,"",e.category,e.note);
            for(GarageDb.Trip t:db.trips(v.id))
                csv(b,"trip",v.id,v.name,iso(t.date),t.startKm,t.endKm,"","","","",t.note);
        }
        return b.toString();
    }
    private static void csv(StringBuilder b,Object...cells) {
        for(int i=0;i<cells.length;i++){
            if(i>0)b.append(',');
            String s=String.valueOf(cells[i]==null?"":cells[i]);
            if(cells[i] instanceof String && !s.isEmpty() && "=+-@".indexOf(s.charAt(0))>=0)s="'"+s;
            b.append('"').append(s.replace("\"","\"\"")).append('"');
        }
        b.append('\n');
    }
    private void toast(String s){Toast.makeText(this,s,Toast.LENGTH_LONG).show();}
    private static String usd(double v){return String.format(Locale.US,"$%,.2f",v);}
    private static String fmt(double v){return String.format(Locale.US,"%,.1f",v);}
    private static String day(long v){return new SimpleDateFormat("MMM d, yyyy",Locale.US).format(new Date(v));}
    private static String iso(long v){return new SimpleDateFormat("yyyy-MM-dd",Locale.US).format(new Date(v));}
    private static String join(String a,String b){
        String s=(a+" "+b).trim();
        return s.isEmpty()?"Vehicle":s;
    }
}
